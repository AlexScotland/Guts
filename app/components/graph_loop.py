"""GraphLoop (alias ``loop.graph``): run a validated graph as an AgentLoop.

Registry-client pattern (same as the other loops): the loop receives the
per-turn component snapshot (``LoopComponents``) and never touches the
registry itself, except for the ``primitives`` namespace — one door — and
the graph store that holds the active JSON document.

Execution semantics (frozen v0 contract):
  * fresh execution, serial top-level walk in deterministic data-edge
    topological order (the engine is the only thing that ever commits to
    the shared conversation, and it does so serially);
  * ``control`` edges (retries) only re-run the declared suffix, at most
    ``max_passes`` times, and only when the verdict did not pass;
  * ``parallel`` branches run concurrently with a fresh blackboard each;
    only a declared ``merge`` node commits branch output, in declared order;
    a branch's RUNTIME failure degrades THAT branch to an empty slot
    (siblings finish, the turn completes, one visible ``branch_failed``
    event per degraded branch), while contract violations — a binding
    budget, an incomplete node environment, run-level misconfiguration —
    still fail the whole run (fail-fast is the default);
  * budgets (executor-budget units): per-node, per-branch and graph
    ceilings; the graph ceiling is binding; exceed → fail-fast;
  * per-node ``on_failure``: abort (default) | skip | retry(n);
  * routing (v1, docs/ai-routing-nodes.md): a ``route`` node resolves its
    decider to ONE outgoing edge — the chosen subtree runs, the untaken
    subtrees are skipped ("not routed", transitively); at a re-join, a
    required input with no ACTIVE provider is skipped ("upstream pruned"),
    never a failure. Graphs without a route node walk exactly as before;
  * every node emits start/result/done (+ failure/skipped/retry) events,
    JSON-serializable, recorded on the state and the graph store — the
    API + future-canvas seam;
   * the ``result`` event carries the node's ACTUAL outputs (``outputs``
     payload + sibling ``truncated`` map; see docs/ai-per-node-outputs.md):
     capped per EVENT, head+tail kept, cap-honest ``str()`` fallback.
"""
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from langchain_core.messages import SystemMessage

from state import AgentState, AgentStatus
from memory import ConversationMemory
from schemas.graph import (
    DEFAULT_MAX_PARALLEL,
    DEFAULT_NODE_BUDGET,
    GraphDoc,
    NodeSpec,
    PRIMITIVES,
    validate_graph,
)

from .graph_store import get_graph_store
from .loops import AgentLoop, LoopComponents
from .primitives.base import NodeConfigError, NodeInputError, describe_error

_MISSING = object()


class GraphLoopError(RuntimeError):
    """The graph loop is misconfigured (no registry / store / active graph)."""


class GraphBudgetError(RuntimeError):
    """A budget ceiling (node/branch/graph) would be exceeded."""


#: The route node's decision costs exactly one executor-budget unit (v1).
ROUTE_DECISION_BUDGET = 1


def _loop_running() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def _verdict_failed(outputs: dict) -> bool:
    """A control edge is 'taken' when the source's verdict did not pass."""
    if not outputs:
        return False
    if "pass" in outputs:
        return outputs["pass"] is False
    verdict = outputs.get("verdict")
    if isinstance(verdict, dict):
        return verdict.get("pass") is False
    if verdict is not None and hasattr(verdict, "decision"):
        return str(verdict.decision).lower() == "fail"
    return False


class EventSink:
    """Thread-safe collector of JSON-serializable node events on the state.

    ``on_event`` (optional) mirrors each event to the graph store's live-run
    record the moment it is emitted — the canvas seam's live-half (see
    docs/graph-loop-designer.md, "Observability"): the API + future-canvas.
    It runs OUTSIDE the sink lock (callback first, then record) so a slow
    mirror can never deadlock the engine; the callback is expected to be
    cheap and lock-free on its side (GraphStore's own RLock covers it).
    """

    def __init__(self, state, on_event=None):
        self.state = state
        self._on_event = on_event
        self._lock = threading.Lock()
        state.metadata.setdefault("graph_events", [])

    def append(self, record: dict):
        with self._lock:
            self.state.metadata["graph_events"].append(record)
            event = self._on_event
        if event is not None:
            event(record)

    def events(self) -> list[dict]:
        with self._lock:
            return list(self.state.metadata["graph_events"])


class OutputLog:
    """The per-run blackboard: (node_id, typed outputs) in execution order.

    Input resolution is 'most recently executed upstream provider' — which
    is exactly what makes re-run suffixes (retry loops) see the FRESH values.
    """

    def __init__(self, seed: dict | None = None):
        self.entries: list[tuple[str, dict]] = []
        if seed:
            self.entries.append(("__context__", dict(seed)))

    def record(self, node_id: str, outputs: dict):
        self.entries.append((node_id, dict(outputs)))

    def latest(self, node_id: str) -> dict | None:
        for nid, outputs in reversed(self.entries):
            if nid == node_id:
                return outputs
        return None

    def resolve(self, type_name: str):
        for _, outputs in reversed(self.entries):
            if type_name in outputs:
                return outputs[type_name]
        return _MISSING


class BudgetLedger:
    """One budget ceiling (executor-budget units), thread-safe."""

    def __init__(self, cap: int | None):
        self.cap = cap
        self.used = 0
        self._lock = threading.Lock()

    def allocate(self, amount: int, who: str = ""):
        if not amount or amount <= 0:
            return
        with self._lock:
            if self.cap is not None and self.used + amount > self.cap:
                raise GraphBudgetError(
                    f"node {who!r} needs {amount} executor-budget unit(s); ceiling "
                    f"{self.cap} would be exceeded ({self.used} already allocated)"
                )
            self.used += amount


class NodeState:
    """The ``state`` a node's ``run(state, config)`` receives (contract door).

    Read ``requires`` with ``get`` / ``get_optional``, record ``provides``
    with ``out``. ``goal`` is the graph input (resolved Goal of the turn).
    Branch nodes operate on their OWN fresh blackboard (fresh conversation
    store); only top-level commits and declared ``merge`` nodes touch the
    shared conversation.
    """

    def __init__(self, engine: "_GraphEngine", node: NodeSpec):
        self._engine = engine
        self._node = node
        self._outputs: dict = {}

    # ── surface (the 5-line contract) ───────────────────────────
    @property
    def node_id(self) -> str:
        return self._node.id

    @property
    def node_budget(self) -> int:
        return self._node.budget if self._node.budget is not None else DEFAULT_NODE_BUDGET

    @property
    def agent(self):
        return self._engine.state

    @property
    def goal(self):
        return self._engine.state.goal

    @property
    def conversation(self):
        return self._engine.state.conversation

    @property
    def ctx(self):
        return self._engine.loop.context

    @property
    def components(self):
        return self._engine.components

    @property
    def max_parallel(self) -> int:
        return self._engine.graph.max_parallel

    @property
    def branches(self):
        return tuple(self._node.branches)

    # ── blackboard ───────────────────────────────────────────────
    def get(self, type_name: str):
        if type_name == "goal":
            if self.goal is None:
                raise NodeInputError(
                    f"node {self.node_id!r} requires the graph input 'goal', but no goal is resolved"
                )
            return self.goal
        value = self._engine.log.resolve(type_name)
        if value is _MISSING:
            raise NodeInputError(
                f"node {self.node_id!r} requires input {type_name!r}, but no upstream node "
                f"provided it (missing edge, or upstream skipped)"
            )
        return value

    def get_optional(self, type_name: str):
        try:
            return self.get(type_name)
        except NodeInputError:
            return None

    def inputs(self):
        """Upstream (node_id, outputs) pairs in DECLARED edge order (merge)."""
        engine = self._engine
        out = []
        for src in engine.graph.incoming.get(self._node.id, ()):
            value = engine.log.latest(src)
            out.append((src, value if value is not None else {}))
        return out

    def out(self, type_name: str, value):
        self._outputs[type_name] = value

    def outputs(self) -> dict:
        return dict(self._outputs)

    def run_branches(self, branches, max_parallel: int, context):
        return self._engine.run_branches(self._node, branches, max_parallel, context)


# ── per-node output payloads (the "result" event seam) ───────────────────
#
# docs/ai-per-node-outputs.md — decisions from the devil's-advocate pass:
#   * head AND tail are kept — an overlong string shrinks to 16 KB +
#     ``… (dropped N chars) …`` + 16 KB; front-only cuts would drop the
#     CONCLUSION, where LLM output answers tend to live;
#   * the ``str()`` fallback for non-JSON values runs through the same
#     string path — the fallback cannot defeat the caps;
#   * caps are per EVENT (each "result" event serializes with a FRESH
#     budget; a control-edge re-run is a new event, and the per-node
#     multiplier is the user-declared max_passes);
#   * ``truncated`` is a sibling map ``{key: original char length}`` —
#     present only where a key was cut — so ``outputs`` stays a clean
#     renderable map and the UI can badge per output.

STRING_HEAD_KEEP = 16 * 1024
STRING_TAIL_KEEP = 16 * 1024
#: Total emitted characters allowed per "result" event (per event, not per node).
EVENT_OUTPUT_CAP = 128 * 1024
_BUDGET_EXHAUSTED = "(event output budget exhausted — not shown)"


class _OutputBudget:
    """One event's output budget + its per-key truncation notes."""

    def __init__(self, cap: int) -> None:
        self._left = cap
        self.truncated: dict[str, int] = {}

    def can_fit(self, chars: int) -> bool:
        return 0 <= chars <= self._left

    def charge(self, chars: int) -> None:
        self._left -= max(0, chars)

    def note(self, top_key: str, original_chars: int) -> None:
        self.truncated[top_key] = max(self.truncated.get(top_key, 0), original_chars)


def _string_view(text: str) -> tuple[str, bool]:
    """Head + tail view of a string; the second return is "was cut"."""
    if len(text) <= STRING_HEAD_KEEP + STRING_TAIL_KEEP:
        return text, False
    dropped = len(text) - STRING_HEAD_KEEP - STRING_TAIL_KEEP
    return (
        text[:STRING_HEAD_KEEP] + f"… (dropped {dropped} chars) …" + text[-STRING_TAIL_KEEP:],
        True,
    )


def _subtree_chars(value) -> int:
    """Characters of all text a full (unshrunk) serialization would emit."""
    if value is None or isinstance(value, (bool, int, float)):
        return len(repr(value))
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (list, tuple)):
        return sum(_subtree_chars(v) for v in value)
    if isinstance(value, dict):
        return sum(len(str(k)) + 2 + _subtree_chars(v) for k, v in value.items())
    return len(str(value))  # the str() fallback is what would be emitted


def serialize_node_outputs(outputs: dict) -> tuple[dict, dict]:
    """Serialize a node's typed outputs into the result event's payload.

    Returns ``(payload, truncated)``:

    * ``payload`` — JSON-safe, one entry per output type. Strings over
      16 KB + 16 KB keep head+tail with a ``… (dropped N chars) …`` marker;
      non-JSON values fall back to ``str()`` and then run through the SAME
      path (cap-honest); the total emitted per call is bounded by
      :data:`EVENT_OUTPUT_CAP` (budget is FRESH per call — per event).
    * ``truncated`` — ``{output key: original char length}``, present only
      where that key's subtree was cut somewhere. Empty dict otherwise.
    """
    budget = _OutputBudget(EVENT_OUTPUT_CAP)

    def conv(value, top_key: str) -> object:
        if value is None or isinstance(value, (bool, int, float)):
            text = repr(value)
            if not budget.can_fit(len(text)):
                budget.note(top_key, len(text))
                return _BUDGET_EXHAUSTED
            budget.charge(len(text))
            return value
        original = _subtree_chars(value)
        if isinstance(value, dict):
            if not budget.can_fit(2):
                budget.note(top_key, original)
                return _BUDGET_EXHAUSTED
            budget.charge(2 + sum(len(str(k)) + 1 for k in value))
            return {str(k): conv(v, top_key) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            if not budget.can_fit(2):
                budget.note(top_key, original)
                return _BUDGET_EXHAUSTED
            budget.charge(2 + len(value))
            return [conv(v, top_key) for v in value]
        text = value if isinstance(value, str) else str(value)  # cap-honest fallback
        view, cut = _string_view(text)
        if cut:
            budget.note(top_key, len(text))
        if not budget.can_fit(len(view)):
            budget.note(top_key, len(text))
            return _BUDGET_EXHAUSTED
        budget.charge(len(view))
        return view

    payload: dict = {}
    for key in outputs:
        top_key = str(key)
        payload[top_key] = conv(outputs[key], top_key)
    return payload, budget.truncated


class _GraphEngine:
    """One execution of one graph (the root graph, or one parallel branch)."""

    def __init__(
        self,
        loop: "GraphLoop",
        components: LoopComponents,
        state,
        graph: GraphDoc,
        sink: EventSink,
        ledgers: list[BudgetLedger],
        branch: str | None = None,
        seed: dict | None = None,
    ):
        self.loop = loop
        self.components = components
        self.state = state
        self.graph = graph
        self.sink = sink
        self.ledgers = ledgers
        self.branch = branch
        self.log = OutputLog(seed)
        self.taken: dict[int, int] = {}
        self._instances: dict[str, object] = {}
        # Active-dataflow state (v1 routing): skip reasons per node and the
        # route node's taken edge (route id -> chosen target id). Both are
        # populated as the walk proceeds; a re-run suffix recomputes them in
        # order as it goes.
        self._skip_reasons: dict[str, str] = {}
        self._route_taken: dict[str, str | None] = {}

    # ── events ───────────────────────────────────────────────────
    def _emit(self, event: str, node: NodeSpec | None = None, detail: str | None = None, **fields):
        record = {"event": event, "at": round(time.time(), 3)}
        if node is not None:
            record["node"] = node.id
        if self.branch is not None:
            record["branch"] = self.branch
        if detail:
            record["detail"] = str(detail)[:2000]
        for name, value in fields.items():
            if value:  # additive fields (outputs/truncated): omitted when empty
                record[name] = value
        self.sink.append(record)

    # ── node execution + failure policy ──────────────────────────
    def _instance(self, node: NodeSpec):
        if node.primitive not in self._instances:
            self._instances[node.primitive] = self.loop.registry.primitive(node.primitive)
        return self._instances[node.primitive]

    def _run_node(self, node: NodeSpec) -> dict:
        shape = PRIMITIVES[node.primitive]
        if shape.budgeted:
            if node.budget is not None:
                amount = node.budget
            elif node.primitive == "route":
                amount = ROUTE_DECISION_BUDGET
            else:
                amount = DEFAULT_NODE_BUDGET
            for ledger in self.ledgers:
                ledger.allocate(amount, who=node.id)
        instance = self._instance(node)
        state = NodeState(self, node)
        attempt = 0
        while True:
            self._emit("start", node)
            try:
                instance.run(state, node.config)
            except Exception as exc:
                self._emit("failure", node, detail=f"{type(exc).__name__}: {exc}")
                if node.on_failure == "retry" and attempt < node.retries:
                    attempt += 1
                    self._emit("retry", node, detail=f"attempt {attempt + 1} of {node.retries + 1}")
                    continue
                if node.on_failure == "skip":
                    self._emit("skipped", node, detail=str(exc))
                    return {}
                self._emit("aborted", node, detail=str(exc))
                raise
            outputs = state.outputs()
            payload, truncated = serialize_node_outputs(outputs)
            self._emit(
                "result",
                node,
                detail=(", ".join(sorted(outputs)) or "(no outputs)"),
                outputs=payload or None,
                truncated=truncated or None,
            )
            self._emit("done", node)
            return outputs

    # ── active dataflow (v1 routing) ─────────────────────────────
    #
    # docs/ai-routing-nodes.md, "Engine semantics": an edge is active iff its
    # source node ran; a node runs iff at least one incoming edge is active
    # (the entry node is unconditional). The SKIP reasons are the pinned
    # vocab:
    #   "not routed"      — inside an untaken route branch (transitively);
    #   "upstream pruned" — a re-join whose required inputs have no ACTIVE
    #                       provider: a declared alternative, not a failure.
    # A graph with no route node never accumulates skip reasons, so the walk
    # stays byte-identical to v0 (including the pinned determinism tests).

    def _skip_reason(self, node: NodeSpec):
        """The routing-skip reason for ``node``, or ``None`` if it runs."""
        graph = self.graph
        sources = graph.incoming.get(node.id, [])
        if node.id == graph.entry or not sources:
            return None
        reasons = [self._skip_reasons.get(s) for s in sources]
        # (1) A route directly upstream did not pick this edge: this node is
        # in the untaken branch (or its transitive subtree).
        for src in sources:
            spec = graph.node(src)
            if spec.primitive == "route" and self._route_taken.get(src) != node.id:
                return "not routed"
        skipped = [r for r in reasons if r is not None]
        if not skipped:
            # (2) No pruned source: the frozen v0 behavior — the node runs and
            # a genuinely missing input surfaces as before (NodeInputError,
            # the node's own failure policy).
            return None
        if len(skipped) == len(reasons):
            # (3) Every active path through this node was pruned: propagate.
            return "not routed" if all(r == "not routed" for r in skipped) else "upstream pruned"
        # (4) Convergence: active and pruned sources re-join here. A required
        # input with no active provider is a clean skip — the pruned branch is
        # a declared alternative, not an error.
        if self._inputs_missing(node):
            return "upstream pruned"
        return None

    def _inputs_missing(self, node: NodeSpec) -> bool:
        """True if any required input of ``node`` has no active provider."""
        shape = PRIMITIVES[node.primitive]
        goal_ok = self.state.goal is not None

        def provided(t: str) -> bool:
            return (t == "goal" and goal_ok) or self.log.resolve(t) is not _MISSING

        return (
            any(not provided(t) for t in shape.requires)
            or any(all(not provided(t) for t in group) for group in shape.requires_any)
        )

    # ── the deterministic walk ───────────────────────────────────
    def run(self):
        state = self.state
        if self.branch is None:
            if state.goal is None:
                raise GraphLoopError(
                    "graph needs the resolved goal (goal_resolver slot) before it runs"
                )
            state.add_goal()
            state.transition_to(AgentStatus.EXECUTING)

        graph = self.graph
        ids = graph.topo
        specs = [graph.node(n) for n in ids]
        index = {n: i for i, n in enumerate(ids)}

        i = 0
        while i < len(specs):
            node = specs[i]
            reason = self._skip_reason(node)
            if reason is not None:
                # Untaken route branch (or its subtree / a re-join with no
                # active provider): a clean skip with a visible detail —
                # NOT a failure, no new event kind.
                self._skip_reasons[node.id] = reason
                self._emit("skipped", node, detail=reason)
                i += 1
                continue
            outputs = self._run_node(node)
            self.log.record(node.id, outputs)
            if node.primitive == "route":
                # One outgoing edge is taken; the rest are pruned by the
                # _skip_reason check when their subtree is reached.
                self._route_taken[node.id] = outputs.get("choice")

            jumped = False
            for eidx in graph.control_out.get(node.id, ()):
                if _verdict_failed(outputs):
                    edge = graph.edges[eidx]
                    cap = edge.max_passes or 0
                    if self.taken.get(eidx, 0) < cap:
                        self.taken[eidx] = self.taken.get(eidx, 0) + 1
                        i = index[edge.target]
                        self._emit(
                            "control",
                            node,
                            detail=(
                                f"verdict not passed -> re-run from {edge.target!r} "
                                f"(pass {self.taken[eidx]}/{cap})"
                            ),
                        )
                        jumped = True
                break  # at most one control edge is taken, declared order
            if not jumped:
                i += 1

        return self._pick_answer()

    def _pick_answer(self):
        for _, outputs in reversed(self.log.entries):
            if outputs.get("result") is not None:
                return outputs["result"]
        for _, outputs in reversed(self.log.entries):
            if outputs.get("merged") is not None:
                return outputs["merged"]
        return ""

    # ── parallel branches (concurrent, isolated, bounded) ────────
    #
    # Branch failure containment (the frozen-v0 extension): a branch's
    # RUNTIME failure — an executor/LLM error, a leaked
    # asyncio.CancelledError, anything non-contractual — degrades THAT
    # branch to an empty slot: the siblings finish, the turn completes with
    # what succeeded, and a `branch_failed` event (node + branch + one-line
    # error) lands in the event stream / live record — the canvas seam's
    # visible trace. Without this, one flaky branch 500'd the whole
    # /api/chat turn (the frontend error banner while a sibling was still
    # in flight). CONTRAST: contract/environment violations (a binding
    # budget — "exceed → fail-fast" is the pinned rule — an incomplete node
    # environment, or a run-level misconfiguration) and fatal signals still
    # fail the WHOLE RUN: a misdeclared graph is a defect, not a flaky
    # lookup, and degrading it would hide the problem.
    _fail_fast_branch = (GraphBudgetError, GraphLoopError, NodeConfigError)

    def run_branches(self, node: NodeSpec, branches, max_parallel: int, context):
        n = len(branches)
        workers = max(1, min(int(max_parallel or DEFAULT_MAX_PARALLEL), n))
        outer_state = self.state

        def work(idx: int):
            b = branches[idx]
            label = b.name
            # Fresh blackboard per branch (per-agent memory invariant, extended
            # to branches): concurrent execution can never interleave commits.
            branch_state = AgentState(
                agent_id=f"{outer_state.agent_id}@{label}",
                goal=outer_state.goal,
                conversation=ConversationMemory(),
                metadata={},
            )
            # Full parity with the ROOT conversation, so "stay on task" is a
            # property of the harness, not of which graph/flow is running.
            # The root state carries [SystemMessage, goal banner, ...]; a
            # bare branch previously had NEITHER the persona/grounding
            # (AGENTS.md) NOR the goal — an ungrounded LLM that improvised
            # both (observed live: a branch researched the project's own
            # codebase + memory/PDFs instead of the user's question). Copy
            # the root's system message, then the goal banner, in root order.
            for msg in outer_state.conversation.messages:
                if isinstance(msg, SystemMessage):
                    branch_state.conversation.add(msg)
                    break
            if outer_state.goal is not None:
                branch_state.add_goal()
            ledgers = ([BudgetLedger(b.budget)] if b.budget is not None else []) + self.ledgers
            eng = _GraphEngine(
                self.loop,
                self.components,
                branch_state,
                b.graph,
                sink=self.sink,
                ledgers=ledgers,
                branch=label,
                seed={"context": context} if context else None,
            )
            try:
                return {"name": label, "answer": eng.run(), "error": None}
            except self._fail_fast_branch:
                # Contract violations are RUN-level: fail the whole turn,
                # not just this branch (fail-fast is the pinned default).
                raise
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:  # noqa: BLE001 — containment is the point
                # A runtime failure (including a leaked asyncio.CancelledError
                # — a BaseException that used to escape past `except Exception`
                # and 500 the endpoint with no diagnostic) is a BRANCH-level
                # outcome: this slot degrades to None, the siblings finish.
                return {"name": label, "answer": None, "error": describe_error(exc)}

        if _loop_running():
            # Inside a running event loop (FastAPI async path): thread pool.
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="graph-branch") as pool:
                records = list(pool.map(work, range(n)))
        else:
            # Sync context (REPL/tests): asyncio to keep collection order declared.
            records = asyncio.run(self._async_map(work, n, workers))

        # One visible event per degraded branch, attributed to the parallel
        # node (the canvas seam keys on `node`; the `branch` tag names the
        # branch). Emitted AFTER collection, so it always follows the
        # branch's own failure/aborted events in the stream.
        for rec in records:
            if rec["error"] is not None:
                self._emit(
                    "branch_failed",
                    node,
                    branch=rec["name"],
                    detail=rec["error"],
                )
        # Declared order, with one (possibly None) slot per branch — the
        # merge node commits whatever succeeded and skips the rest.
        return [rec["answer"] for rec in records]

    @staticmethod
    async def _async_map(work, n: int, workers: int):
        sem = asyncio.Semaphore(workers)

        async def one(idx: int):
            async with sem:
                return await asyncio.to_thread(work, idx)

        return list(await asyncio.gather(*(one(idx) for idx in range(n))))


class GraphLoop(AgentLoop):
    """Declarative loop topology: the active graph (JSON) IS the loop.

    Activate it like any loop alias — with a graph id attached:
        POST /api/components/activate  {"loop": "graph", "graph_id": "..."}
    The graph document itself travels through the ``/api/graphs`` endpoints
    (saved as a JSON file — the store).
    """

    name = "graph"
    version = "1.0.0"

    def __init__(self, registry=None, context=None, graph_store=None):
        self.registry = registry
        self.context = context
        self._store = graph_store

    def resolve_store(self):
        if self._store is not None:
            return self._store
        if self.context is not None:
            stored = (self.context.extras or {}).get("graph_store")
            if stored is not None:
                self._store = stored
                return stored
        return get_graph_store()

    def run(self, components: LoopComponents, state):
        if self.registry is None:
            raise GraphLoopError("the graph loop needs a ComponentRegistry (the primitives namespace)")
        store = self.resolve_store()
        graph_id = store.active_id
        if graph_id is None:
            raise GraphLoopError(
                "no active graph — save one (POST /api/graphs) then activate with "
                "{\"loop\": \"graph\", \"graph_id\": \"...\"}"
            )
        doc = store.get(graph_id)
        graph = validate_graph(doc)  # belt + braces: a stored doc must still run
        # Live-run mirror (the canvas seam): every event also flows to the
        # store WHILE the run is in flight, so GET /api/graphs/{id}/last-run
        # reports the current node — then the finished record replaces it.
        store.begin_run(graph_id)
        sink = EventSink(state, on_event=lambda rec: store.append_event(graph_id, rec))
        engine = _GraphEngine(
            self, components, state, graph, sink=sink, ledgers=[BudgetLedger(graph.budget)]
        )
        try:
            answer = engine.run()
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as exc:
            # Catch BaseException, not just Exception: a leaked cancellation
            # (asyncio.CancelledError from a cancelled parallel branch) or
            # other non-Exception failure still has to be (a) recorded with
            # its type name so the canvas seam / `last-run` / the endpoint
            # 500 all carry a diagnostic, and (b) the state marked FAILED.
            # Without the BaseException catch, an unhandled exception fell
            # through to the framework's bare 500 (empty body) and no run
            # record was written at all.
            state.transition_to(AgentStatus.FAILED)
            store.record_run(
                graph_id,
                {"status": "failed", "error": describe_error(exc), "events": sink.events()},
            )
            store.clear_live(graph_id)
            raise
        if isinstance(answer, (list, tuple)):
            answer = "\n\n".join(str(a) for a in answer if a is not None)
        state.transition_to(AgentStatus.COMPLETED)
        store.record_run(graph_id, {"status": "ok", "answer": answer, "events": sink.events()})
        store.clear_live(graph_id)
        return answer
