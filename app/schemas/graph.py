"""Graph documents: agent-loop orchestration declared as data.

A *graph* is a JSON document (a dict, a file path, or JSON text — parsed
exactly like manifests today) describing which primitives run, in what order,
with which branches and retries. This module is the single source of truth
for the frozen v0 contract in docs/graph-loop-designer.md:

  * a SMALL closed vocabulary (the 8 v0 primitives + ``route``, the v1
    extension from docs/ai-routing-nodes.md) with fixed port shapes;
  * the closed require/provide type set: context, goal, plan, result,
    verdict, merged, pass — `goal` is the GRAPH INPUT (the resolved Goal)
    and needs no incoming edge;
  * every edge type-checks against the source's ``provides`` and the
    target's accepted inputs;
  * exactly ONE entry node (zero incoming data edges; its required inputs
    satisfied by the graph input), and every node reachable from it over
    data edges;
  * data edges are acyclic; cycles exist only through declared ``control``
    edges, and every control edge (a declared repeat) carries
    ``max_passes >= 1``;
  * control edges only leave verdict nodes (``critic`` / ``gate``);
  * ``parallel`` nodes declare 1+ INLINE branch graphs, each recursively
    valid, and NO side-effect node (``act``) may run inside a parallel
    branch (correctness property: the engine serializes conversation
    commits; branches have their own fresh blackboard);
  * per-node ``on_failure``: ``abort`` (default, fail-fast) | ``skip`` |
    ``retry(n)``;
  * budgets, unit = executor budget: optional per-node, per-branch and
    per-graph ceilings — the graph ceiling is binding;
  * concurrency is only ever inside a declared ``parallel`` group, capped
    by ``max_parallel`` (default 4).

Validation is PURE (no LLM, no I/O): the same rules hold for top-level
graphs and for every inline branch, so a graph is inert until a
``parallel``-free engine actually runs it — and a document that cannot be
validated cannot be saved, activated, or executed.
"""
import heapq
import json
import re
from dataclasses import dataclass, field
from typing import Any

#: The closed primitive vocabulary: the 8 v0 primitives + ``route`` (v1,
#: docs/ai-routing-nodes.md) — the 9th entry, shippable standalone.
KNOWN_PRIMITIVES = (
    "prompt", "research", "plan", "act", "critic", "gate", "merge", "parallel", "route",
)

#: Closed input edge type: the resolved Goal is the standard graph input.
GRAPH_INPUTS = ("goal",)

DEFAULT_MAX_PARALLEL = 4
#: Allocation a budgeted node consumes when it declares no explicit budget.
DEFAULT_NODE_BUDGET = 6

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class GraphError(ValueError):
    """A graph document violates the frozen v0 contract."""


@dataclass(frozen=True)
class PortShape:
    """The fixed typing of one primitive (provides / accepted inputs)."""

    requires: tuple = ()          # mandatory typed inputs
    requires_any: tuple = ()      # disjunction groups: any one member suffices
    optional: tuple = ()          # optional typed inputs
    provides: tuple = ()          # typed outputs recorded on the blackboard
    accepts: tuple = ()           # types an incoming data edge may carry
    side_effects: bool = False    # mutates world/conversation — no parallel
    budgeted: bool = False        # consumes executor-budget units


#: The v0 vocabulary (docs/graph-loop-designer.md, "v0 vocabulary").
#:
#:   prompt    - inject a fixed prompt; context in -> context out
#:   research  - tool-calling, read-only context gathering (executor-backed)
#:   plan      - the active planner slot -> task list
#:   act       - the active executor slot; one 'plan' OR 'context' in; 'result' out
#:   critic    - heuristic evaluator first, then LLM-with-schema judgment
#:   gate      - deterministic verdict -> pass projection
#:   merge     - commits upstream outputs to the conversation, in declared order
#:   parallel  - inline branch graphs, asyncio-concurrent, branch-local blackboards
#:   route     - (v1) dynamic choice among its outgoing data edges (the choice
#:               set); manual | llm decider, {choice, reason} out, the incoming
#:               payload passed through as context + result (docs/ai-routing-nodes.md)
PRIMITIVES: dict[str, PortShape] = {
    "prompt": PortShape(
        provides=("context",),
        accepts=("goal",),
    ),
    "research": PortShape(
        requires=("goal",),
        accepts=("goal", "context"),
        provides=("context", "result"),
        budgeted=True,
    ),
    "plan": PortShape(
        requires=("goal",),
        accepts=("goal", "context"),
        provides=("plan",),
    ),
    "act": PortShape(
        requires_any=(("plan", "context"),),
        accepts=("plan", "context", "goal"),
        provides=("result",),
        side_effects=True,
        budgeted=True,
    ),
    "critic": PortShape(
        requires=("result",),
        accepts=("result", "goal"),
        provides=("verdict", "pass"),
    ),
    "gate": PortShape(
        requires=("verdict",),
        accepts=("verdict",),
        provides=("pass",),
    ),
    "merge": PortShape(
        accepts=("result", "merged"),
        provides=("merged", "result", "context"),
    ),
    "parallel": PortShape(
        requires=("goal",),
        accepts=("goal", "context"),
        provides=("merged",),
    ),
    "route": PortShape(
        accepts=("goal", "context", "result"),
        provides=("context", "result"),   # passthrough, research-style duality
        budgeted=True,
    ),
}


# ── parsed shapes ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class BranchSpec:
    """One inline parallel branch: name + budget ceiling + its own graph."""

    name: str
    budget: int | None
    graph: "GraphDoc"


@dataclass(frozen=True)
class NodeSpec:
    id: str
    primitive: str
    config: dict
    budget: int | None
    on_failure: str                 # "abort" | "skip" | "retry"
    retries: int                    # retries allowed when on_failure == "retry"
    branches: tuple                 # tuple[BranchSpec] — parallel nodes only


@dataclass(frozen=True)
class EdgeSpec:
    source: str
    target: str
    kind: str                       # "data" | "control"
    max_passes: int | None          # required for control edges


@dataclass(frozen=True)
class GraphDoc:
    """A validated, normalized graph (top level or one parallel branch)."""

    name: str
    version: str
    budget: int | None              # graph ceiling (binding), executor-budget units
    max_parallel: int
    nodes: tuple                    # tuple[NodeSpec] in declared order
    edges: tuple                    # tuple[EdgeSpec] in declared order
    entry: str                      # the single entry node id
    topo: tuple                     # deterministic data-edge topological order
    incoming: dict                  # node id -> (upstream ids, declared order)
    control_out: dict               # node id -> (control edge indices, declared order)
    _node_map: dict = field(default_factory=dict, repr=False, compare=False)

    def node(self, node_id: str) -> NodeSpec:
        return self._node_map[node_id]


# ── scalar parsers ───────────────────────────────────────────────────────────


def _validate_id(value: Any, label: str):
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise GraphError(
            f"{label}: id must be a non-empty [A-Za-z0-9_-] token of at most 64 chars; got {value!r}"
        )


def _parse_nonneg_int(value: Any, label: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GraphError(f"{label}: expected an integer >= {minimum}; got {value!r}")
    return value


def _parse_on_failure(raw: Any, label: str) -> tuple[str, int]:
    """Normalize ``on_failure`` to (policy, retries): abort/skip => 0 retries."""
    if raw is None:
        return ("abort", 0)
    if isinstance(raw, str):
        s = raw.strip().lower()
        if s in ("abort", "skip"):
            return (s, 0)
        m = re.fullmatch(r"retry\(\s*(\d+)\s*\)", s)
        if m:
            return ("retry", int(m.group(1)))
        raise GraphError(
            f"{label}: on_failure must be 'abort', 'skip', or 'retry(n)'; got {raw!r}"
        )
    if isinstance(raw, dict):
        op = str(raw.get("op", "")).strip().lower()
        if op not in ("abort", "skip", "retry"):
            raise GraphError(f"{label}: on_failure op must be 'abort'|'skip'|'retry'; got {raw!r}")
        count = raw.get("count", 1)
        if op == "retry":
            return ("retry", _parse_nonneg_int(count, f"{label}.on_failure.count", 1))
        return (op, 0)
    raise GraphError(
        f"{label}: on_failure must be 'abort', 'skip', 'retry(n)' (or an object); got {raw!r}"
    )


# ── core validation ──────────────────────────────────────────────────────────


def _validate_graph(data: Any, label: str, in_branch: bool) -> GraphDoc:
    if not isinstance(data, dict):
        raise GraphError(f"{label}: a graph must be a JSON object")

    nodes_raw = data.get("nodes")
    if not isinstance(nodes_raw, dict) or not nodes_raw:
        raise GraphError(
            f"{label}: 'nodes' must be a non-empty object mapping node id -> node spec"
        )
    edges_raw = data.get("edges", [])
    if not isinstance(edges_raw, list):
        raise GraphError(f"{label}: 'edges' must be a list")
    if "name" in data and not isinstance(data.get("name"), str):
        raise GraphError(f"{label}: 'name' must be a string")

    name = data.get("name") or label
    version = str(data.get("version", "1.0"))
    budget = data.get("budget")
    budget = _parse_nonneg_int(budget, f"{label}.budget", 0) if budget is not None else None
    graph_mp = data.get("max_parallel")
    if graph_mp is None:
        graph_mp = DEFAULT_MAX_PARALLEL
    mp = _parse_nonneg_int(graph_mp, f"{label}.max_parallel", 1)

    # ── nodes ──
    nodes: dict[str, NodeSpec] = {}
    for nid, spec in nodes_raw.items():
        _validate_id(nid, f"{label}.nodes")
        nlabel = f"{label} node {nid!r}"
        if not isinstance(spec, dict):
            raise GraphError(f"{nlabel}: a node spec must be an object")
        prim = spec.get("primitive")
        if prim not in PRIMITIVES:
            raise GraphError(
                f"{nlabel}: unknown primitive {prim!r} "
                f"(available: {', '.join(KNOWN_PRIMITIVES)})"
            )
        if in_branch and PRIMITIVES[prim].side_effects:
            raise GraphError(
                f"{nlabel}: side-effect node {nid!r} ({prim!r}) is not allowed "
                f"inside a parallel branch (conversation commits must stay serial)"
            )
        config = spec.get("config", {})
        if not isinstance(config, dict) or any(not isinstance(k, str) for k in config):
            raise GraphError(f"{nlabel}: 'config' must be an object with string keys")
        node_budget = spec.get("budget")
        node_budget = (
            _parse_nonneg_int(node_budget, f"{nlabel}.budget", 0)
            if node_budget is not None
            else None
        )
        policy, retries = _parse_on_failure(spec.get("on_failure"), nlabel)

        branches: tuple = ()
        if spec.get("branches") is not None:
            if prim != "parallel":
                raise GraphError(f"{nlabel}: 'branches' is only valid on 'parallel' nodes")
            blist = spec["branches"]
            if not isinstance(blist, list) or not blist:
                raise GraphError(
                    f"{nlabel}: parallel nodes need a non-empty 'branches' list of "
                    f"{{'name'?, 'budget'?, 'graph': <inline graph>}}"
                )
            parsed = []
            for i, b in enumerate(blist):
                if isinstance(b, dict) and isinstance(b.get("name"), str):
                    blabel = f"{label} branch {b['name']!r}"
                else:
                    blabel = f"{label} branch {i}"
                if (
                    not isinstance(b, dict)
                    or not isinstance(b.get("graph"), dict)
                ):
                    raise GraphError(f"{blabel}: each branch must be an object with an inline 'graph'")
                bbudget = b.get("budget")
                bbudget = (
                    _parse_nonneg_int(bbudget, f"{blabel}.budget", 0)
                    if bbudget is not None
                    else None
                )
                sub = _validate_graph(b["graph"], blabel, in_branch=True)
                parsed.append(
                    BranchSpec(name=b.get("name") or str(i), budget=bbudget, graph=sub)
                )
            branches = tuple(parsed)

        nodes[nid] = NodeSpec(
            id=nid,
            primitive=prim,
            config=dict(config),
            budget=node_budget,
            on_failure=policy,
            retries=retries,
            branches=branches,
        )

    # ── edges ──
    edges: list[EdgeSpec] = []
    for i, e in enumerate(edges_raw):
        elabel = f"{label}.edges[{i}]"
        if not isinstance(e, dict):
            raise GraphError(f"{elabel}: each edge must be an object {{from, to, type?, max_passes?}}")
        src, dst = e.get("from"), e.get("to")
        if src is None or dst is None:
            raise GraphError(f"{elabel}: edges need both 'from' and 'to'")
        _validate_id(src, f"{elabel}.from")
        _validate_id(dst, f"{elabel}.to")
        if src not in nodes or dst not in nodes:
            unknown = src if src not in nodes else dst
            raise GraphError(
                f"{elabel}: references unknown node {unknown!r} (declared: {', '.join(nodes)})"
            )
        if src == dst:
            raise GraphError(f"{elabel}: an edge cannot connect a node to itself")
        kind = e.get("type", "data")
        max_passes = None
        if kind == "control":
            src_provides = PRIMITIVES[src and nodes[src].primitive].provides
            if "pass" not in src_provides and "verdict" not in src_provides:
                raise GraphError(
                    f"{elabel}: control edges must leave a verdict node "
                    f"('critic' or 'gate'); {src!r} ({nodes[src].primitive!r}) provides {list(src_provides)}"
                )
            max_passes = _parse_nonneg_int(
                e.get("max_passes"), f"{elabel}.max_passes", 1
            )
        elif kind != "data":
            raise GraphError(f"{elabel}: edge 'type' must be 'data' or 'control'; got {kind!r}")
        else:
            shared = set(PRIMITIVES[nodes[src].primitive].provides) & set(
                PRIMITIVES[nodes[dst].primitive].accepts
            )
            if not shared:
                raise GraphError(
                    f"{elabel}: no compatible ports — {src!r} provides {list(PRIMITIVES[nodes[src].primitive].provides)}, "
                    f"{dst!r} accepts {list(PRIMITIVES[nodes[dst].primitive].accepts)}"
                )
        edges.append(EdgeSpec(source=src, target=dst, kind=kind, max_passes=max_passes))

    # ── route node wiring rules (v1, docs/ai-routing-nodes.md) ──
    # The choice set (outgoing data edges) and the decider parts must line
    # up EXACTLY: every option is a wired branch, every wired branch is an
    # option. Checked here (after edges) and recursively — a route inside a
    # parallel branch is validated by the same rules.
    for nid, spec in nodes.items():
        if spec.primitive != "route":
            continue
        nlabel = f"{label} node {nid!r}"
        downstream = [e.target for e in edges if e.kind == "data" and e.source == nid]
        if len(downstream) < 2:
            raise GraphError(
                f"{nlabel}: 'route' needs at least two outgoing data edges (its "
                f"choice set); found {len(downstream)} — one target is a plain edge"
            )
        options = spec.config.get("options")
        if options is None:
            raise GraphError(
                f"{nlabel}: route needs 'config.options' mapping each choice-set "
                f"branch id to a non-empty description"
            )
        if not isinstance(options, dict) or not options:
            raise GraphError(f"{nlabel}: 'config.options' must be a non-empty object (id -> description)")
        downstream_set = set(downstream)
        choices = [k for k in options if isinstance(k, str)]
        if set(choices) != downstream_set:
            extra = sorted(set(choices) - downstream_set)
            missing = sorted(downstream_set - set(choices))
            if extra and not missing:
                raise GraphError(
                    f"{nlabel}: config.options key {extra[0]!r} has no outgoing data edge "
                    f"(choice set is {sorted(downstream_set)}); an option must be a wired branch"
                )
            if missing and not extra:
                raise GraphError(
                    f"{nlabel}: choice set is missing config.options for downstream node "
                    f"{missing[0]!r} (options are {sorted(set(choices))})"
                )
            raise GraphError(
                f"{nlabel}: config.options keys {sorted(set(choices))} must equal the "
                f"downstream node ids {sorted(downstream_set)}"
            )
        for key in choices:
            if not isinstance(options[key], str) or not options[key].strip():
                raise GraphError(
                    f"{nlabel}: config.options[{key!r}] must be a non-empty string "
                    f"(the branch description the decider and the canvas show); got {options[key]!r}"
                )
        decider = spec.config.get("decider")
        if decider == "rule":
            raise GraphError(f"{nlabel}: decider 'rule' is reserved (unimplemented)")
        if decider not in ("manual", "llm"):
            raise GraphError(
                f"{nlabel}: route needs config.decider = 'manual' or 'llm' "
                f"('rule' is reserved — got {decider!r})"
            )
        fallback = spec.config.get("fallback")
        if fallback is not None and fallback not in choices:
            raise GraphError(
                f"{nlabel}: config.fallback {fallback!r} is not in the choice set {sorted(downstream_set)}"
            )
        if decider == "manual":
            pinned = spec.config.get("pinned")
            if pinned is None:
                raise GraphError(
                    f"{nlabel}: the manual decider needs config.pinned naming one of "
                    f"the choice set {sorted(downstream_set)}"
                )
            if pinned not in choices:
                raise GraphError(
                    f"{nlabel}: config.pinned {pinned!r} is not in the choice set {sorted(downstream_set)}"
                )
        elif "pinned" in spec.config:
            raise GraphError(f"{nlabel}: config.pinned is only valid with the manual decider")

    # ── entry, reachability, determinism (data edges only) ──
    indeg = {nid: 0 for nid in nodes}
    adj: dict[str, list[str]] = {nid: [] for nid in nodes}
    for e in edges:
        if e.kind == "data":
            indeg[e.target] += 1
            adj[e.source].append(e.target)

    entries = [nid for nid in nodes if indeg[nid] == 0]
    if len(entries) != 1:
        detail = "; ".join(
            f"{n!r} (requires {list(PRIMITIVES[nodes[n].primitive].requires)})" for n in entries
        )
        raise GraphError(
            f"{label}: expected exactly one entry node (zero incoming data edges, "
            f"inputs satisfiable by the graph input 'goal'); found {len(entries)}: {detail or '(none)'}"
        )
    entry = entries[0]
    shape = PRIMITIVES[nodes[entry].primitive]
    unsatisfied = [t for t in shape.requires if t not in GRAPH_INPUTS]
    for group in shape.requires_any:
        if not any(t in GRAPH_INPUTS for t in group):
            unsatisfied.append(" or ".join(group))
    if unsatisfied:
        raise GraphError(
            f"{label}: entry node {entry!r} requires {unsatisfied}, but only the graph "
            f"input 'goal' is available before any edge runs"
        )

    seen = {entry}
    stack = [entry]
    while stack:
        for nxt in adj[stack.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    unreachable = [n for n in nodes if n not in seen]
    if unreachable:
        raise GraphError(
            f"{label}: node(s) {unreachable} are unreachable from entry {entry!r} over data edges"
        )

    indeg2 = dict(indeg)
    heap = [n for n in nodes if indeg2[n] == 0]
    heapq.heapify(heap)
    topo: list[str] = []
    while heap:
        nid = heapq.heappop(heap)
        topo.append(nid)
        for nxt in adj[nid]:
            indeg2[nxt] -= 1
            if indeg2[nxt] == 0:
                heapq.heappush(heap, nxt)
    if len(topo) != len(nodes):
        stuck = [n for n in nodes if indeg2[n] > 0]
        raise GraphError(
            f"{label}: data edges form a cycle through {stuck} — cycles are only "
            f"allowed through declared 'control' edges (type 'control' + max_passes)"
        )

    incoming = {nid: [] for nid in nodes}
    for e in edges:
        if e.kind == "data":
            incoming[e.target].append(e.source)
    control_out = {nid: [] for nid in nodes}
    for i, e in enumerate(edges):
        if e.kind == "control":
            control_out[e.source].append(i)

    doc = GraphDoc(
        name=name,
        version=version,
        budget=budget,
        max_parallel=mp,
        nodes=tuple(nodes.values()),
        edges=tuple(edges),
        entry=entry,
        topo=tuple(topo),
        incoming=incoming,
        control_out=control_out,
    )
    object.__setattr__(doc, "_node_map", {n.id: n for n in doc.nodes})
    return doc


def validate_graph(data) -> GraphDoc:
    """Validate a graph document (must already be parsed to a dict).

    Raises :class:`GraphError` with a human-readable path (top graph /
    branch / node / edge) on the first violation.
    """
    if not isinstance(data, dict):
        raise GraphError("a graph must be a JSON object at the top level")
    return _validate_graph(data, "graph", in_branch=False)


def parse_graph(source):
    """Parse a graph (JSON text, file path, or dict) and validate it.

    Parsed exactly like manifests today: a dict is used as-is, a string that
    looks like JSON is decoded, anything else is treated as a file path.
    """
    if isinstance(source, dict):
        data = source
    elif isinstance(source, str):
        text = source.strip()
        if text.startswith("{"):
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise GraphError(f"graph JSON is invalid: {exc}") from exc
        else:
            try:
                with open(source) as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                raise GraphError(f"Cannot load graph document {source!r}: {exc}") from exc
    else:
        raise GraphError(f"a graph must be a dict, a JSON string, or a file path; got {type(source).__name__}")
    return validate_graph(data)
