"""Tests for the graph loop (v0, docs/graph-loop-designer.md).

Pinned behaviors:
  * schemas/graph.py — the frozen v0 validator: closed vocabulary, typed
    edges, exactly one entry, data-edge acyclicity, control edges only from
    verdict nodes with max_passes, side-effect nodes rejected inside
    parallel branches, on_failure/budget parsing.
  * components/graph_loop.py — execution semantics with FAKE components only
    (frozen v0 test rule: fakes assert; real LLM runs are smoke tests):
    serial walk, control-edge retries, node-level retry/skip/abort,
    budget ceilings, parallel determinism (same graph ⇒ identical
    conversation ORDER across 5 runs), branch isolation, branch failure
    containment (a flaky branch degrades to a NULL slot + a `branch_failed`
    event, the turn still completes `ok`; budget/environment violations
    still fail the whole run), event stream.
  * registry — the primitives namespace (one door) + describe() blocks.

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/ -v
"""
import json
import os
import sys
import threading
import time
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from components import (
    ComponentContext,
    ComponentRegistry,
    ComponentSlot,
    GraphLoop,
    register_builtin_components,
    register_graph_components,
)
from components.graph_loop import (
    EVENT_OUTPUT_CAP,
    GraphBudgetError,
    GraphLoopError,
    serialize_node_outputs,
)
from components.graph_store import GraphStore
from components.loops import LoopComponents
from goals.goal import Goal
from memory import ConversationMemory
from components.primitives.base import NodeBase
from schemas.graph import (
    GraphError,
    KNOWN_PRIMITIVES,
    PRIMITIVES,
    PortShape,
    validate_graph,
)
from state import AgentState
from tools.tool_registry import ToolRegistry

KILLER_GRAPH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "graphs", "killer-research.json"
)


# ── fakes (the frozen v0 test oracle: LLM + executor, never the harness) ────


class FakeStructured:
    def invoke(self, messages):
        return Goal(intent="fake intent")


class FakeLLM:
    """Just enough of the LLMProvider surface for construction."""

    def with_structured_output(self, schema):
        return FakeStructured()

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="fake-llm-answer")


class ScriptedCriticLLM:
    """The critic's LLM seam: a scripted pass/fail sequence (never the harness)."""

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls: list[str] = []
        self._n = 0

    def with_structured_output(self, schema):
        outer = self

        class _S:
            def invoke(self, messages):
                i = min(outer._n, len(outer.decisions) - 1)
                outer._n += 1
                d = outer.decisions[i]
                outer.calls.append(d)
                return types.SimpleNamespace(decision=d, feedback="scripted")

        return _S()


class FakeExecutor:
    """Records the tasks it ran; answers with the last word of the task text."""

    def __init__(self, answer="FROZEN-ANSWER", fail_times=0):
        self.answer = answer
        self.fail_times = fail_times
        self.tasks = []
        self.calls = 0

    def execute(self, task, state):
        self.calls += 1
        self.tasks.append(task)
        if self.calls <= self.fail_times:
            raise RuntimeError("fake executor exploded")
        return self.answer


class FlakyExecutor(FakeExecutor):
    """Fails while ``fail_times`` is true, then recovers — for node retries."""


class GatedExecutor(FakeExecutor):
    """Blocks inside execute() until the gate is set — holds the loop
    MID-RUN (in some node) so the live-run record (the canvas seam) can be
    observed and asserted. Fakes-only rule: the gate is an oracle, the
    harness is what is under test."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.gate = threading.Event()

    def execute(self, task, state):
        self.gate.wait(timeout=10)
        return super().execute(task, state)


def make_env(tmp_path, llm=None, executor=None):
    """A wired registry + store + activated graph loop (no network, no real LLM)."""
    llm = llm or FakeLLM()
    executor = executor or FakeExecutor()
    store = GraphStore(os.path.join(str(tmp_path), "graphs"))
    context = ComponentContext(llm=llm, tools=ToolRegistry(), config=None)
    registry = ComponentRegistry(context)
    register_builtin_components(registry, context)
    register_graph_components(registry, context, graph_store=store)
    registry.activate(ComponentSlot.LOOP, "graph")
    return {
        "registry": registry,
        "store": store,
        "loop": registry.active(ComponentSlot.LOOP),
        "executor": executor,
        "llm": llm,
    }


def run_turn(env, doc, agent_id="agent_under_test"):
    """Save+activate ``doc``, run ONE turn on a fresh AgentState."""
    env["store"].save("turn-graph", doc, name="test graph")
    env["store"].set_active("turn-graph")
    state = AgentState(
        agent_id=agent_id,
        conversation=ConversationMemory(),
        goal=Goal(intent="test goal"),
    )
    components = LoopComponents(executor=env["executor"])
    answer = env["loop"].run(components, state)
    return answer, state


def conversation_signature(state):
    """(type, content) pairs — what the determinism rule pins down."""
    return [(m.type, m.content) for m in state.conversation.messages]


# ── serial workflow graphs ───────────────────────────────────────────────────

SERIAL_GRAPH = {
    "nodes": {
        "prep": {"primitive": "prompt", "config": {"text": "Focus on concrete steps"}},
        "do": {"primitive": "act"},
        "judge": {"primitive": "critic"},
    },
    "edges": [
        {"from": "prep", "to": "do", "type": "data"},
        {"from": "do", "to": "judge", "type": "data"},
    ],
}

RETRY_GRAPH = {
    "nodes": {
        "prep": {"primitive": "prompt", "config": {"text": "Context for the work"}},
        "do": {"primitive": "act"},
        "judge": {"primitive": "critic"},
    },
    "edges": [
        {"from": "prep", "to": "do", "type": "data"},
        {"from": "do", "to": "judge", "type": "data"},
        {"from": "judge", "to": "do", "type": "control", "max_passes": 2},
    ],
}

PARALLEL_GRAPH = {
    "max_parallel": 2,
    "nodes": {
        "fanout": {
            "primitive": "parallel",
            "branches": [
                {
                    "name": "b1",
                    "graph": {
                        "nodes": {"gather1": {"primitive": "research", "config": {"task": "TASK-ONE"}}},
                        "edges": [],
                    },
                },
                {
                    "name": "b2",
                    "graph": {
                        "nodes": {"gather2": {"primitive": "research", "config": {"task": "TASK-TWO"}}},
                        "edges": [],
                    },
                },
            ],
        },
        "join": {"primitive": "merge"},
    },
    "edges": [{"from": "fanout", "to": "join", "type": "data"}],
}


# ── validator: the frozen v0 contract ────────────────────────────────────────


def test_serial_graph_validates():
    doc = validate_graph(SERIAL_GRAPH)
    assert doc.entry == "prep"
    assert doc.topo == ("prep", "do", "judge")


def test_shipped_killer_workflow_validates():
    with open(KILLER_GRAPH_PATH) as fh:
        doc = validate_graph(json.load(fh))
    assert doc.entry == "fanout"
    assert len(doc.control_out["judge"]) == 1
    assert doc.node("judge").id == "judge"


@pytest.mark.parametrize(
    "mutate,expected",
    [
        (
            lambda g: g["nodes"]["prep"].update(primitive="vibecoding"),
            "unknown primitive",
        ),
        (
            lambda g: g["edges"].__setitem__(
                1, {"from": "judge", "to": "do", "type": "control"}
            ),
            "integer >= 1",  # control edge from a verdict node, but max_passes missing
        ),
        (
            lambda g: g["edges"].__setitem__(
                1, {"from": "prep", "to": "do", "type": "control", "max_passes": 2}
            ),
            "control edges must leave a verdict node",
        ),
        (
            lambda g: g["edges"].append({"from": "judge", "to": "prep", "type": "control", "max_passes": 0}),
            "integer >= 1",
        ),
        (
            lambda g: g["nodes"].update({"loner": {"primitive": "prompt", "config": {"text": "x"}}}),
            "expected exactly one entry node",
        ),
    ],
)
def test_contract_violations_are_rejected(tmp_path, mutate, expected):
    doc = json.loads(json.dumps(SERIAL_GRAPH))
    mutate(doc)
    with pytest.raises(GraphError, match=expected):
        validate_graph(doc)


def test_data_cycle_rejected_even_with_single_entry():
    doc = {
        "nodes": {
            "seed": {"primitive": "research"},  # entry: goal-only requires; provides 'result'
            "m": {"primitive": "merge"},
            "p": {"primitive": "parallel", "branches": [{"name": "b", "graph": {"nodes": {"r": {"primitive": "research"}}, "edges": []}}]},
        },
        "edges": [
            {"from": "seed", "to": "m", "type": "data"},  # result -> merge
            {"from": "m", "to": "p", "type": "data"},      # context -> parallel
            {"from": "p", "to": "m", "type": "data"},      # merged -> merge (the cycle)
        ],
    }
    with pytest.raises(GraphError, match="cycle"):
        validate_graph(doc)


def test_zero_entry_graphs_rejected():
    doc = {
        "nodes": {
            "m": {"primitive": "merge"},
            "p": {"primitive": "parallel", "branches": [{"name": "b", "graph": {"nodes": {"r": {"primitive": "research"}}, "edges": []}}]},
        },
        "edges": [
            {"from": "m", "to": "p", "type": "data"},
            {"from": "p", "to": "m", "type": "data"},
        ],
    }
    with pytest.raises(GraphError, match="entry"):
        validate_graph(doc)


def test_incompatible_data_edge_rejected():
    doc = {
        "nodes": {
            "r": {"primitive": "research"},
            "g": {"primitive": "gate"},
        },
        "edges": [{"from": "r", "to": "g", "type": "data"}],
    }
    with pytest.raises(GraphError, match="no compatible ports"):
        validate_graph(doc)


def test_entry_requiring_non_goal_input_rejected():
    with pytest.raises(GraphError, match="graph input 'goal'"):
        validate_graph({"nodes": {"do": {"primitive": "act"}}, "edges": []})


def test_self_edge_rejected():
    with pytest.raises(GraphError, match="itself"):
        validate_graph({"nodes": {"a": {"primitive": "prompt", "config": {"text": "x"}}}, "edges": [{"from": "a", "to": "a"}]})


def test_unknown_edge_target_rejected():
    with pytest.raises(GraphError, match="unknown node"):
        validate_graph(
            {
                "nodes": {
                    "a": {"primitive": "prompt", "config": {"text": "x"}},
                    "do": {"primitive": "act"},
                },
                "edges": [{"from": "a", "to": "ghost", "type": "data"}],
            }
        )


def test_side_effect_node_rejected_inside_parallel_branch():
    doc = json.loads(json.dumps(PARALLEL_GRAPH))  # deep copy: never mutate the shared constant
    doc["nodes"]["fanout"]["branches"][0]["graph"]["nodes"]["actor"] = {"primitive": "act"}
    with pytest.raises(GraphError, match="side-effect"):
        validate_graph(doc)


def test_branches_only_allowed_on_parallel():
    doc = {
        "nodes": {
            "do": {
                "primitive": "act",
                "branches": [{"name": "b", "graph": {"nodes": {"r": {"primitive": "research"}}, "edges": []}}],
            }
        },
        "edges": [],
    }
    with pytest.raises(GraphError, match="only valid on 'parallel'"):
        validate_graph(doc)


def test_branch_must_carry_inline_graph():
    doc = {
        "nodes": {
            "p": {"primitive": "parallel", "branches": [{"name": "b", "graph": "not-a-graph"}]}
        },
        "edges": [],
    }
    with pytest.raises(GraphError, match="inline 'graph'"):
        validate_graph(doc)


def test_on_failure_parsing():
    good = {"nodes": {"research": {"primitive": "research", "on_failure": "retry(2)"}}, "edges": []}
    assert validate_graph(good).node("research").on_failure == "retry"
    assert validate_graph(good).node("research").retries == 2
    with pytest.raises(GraphError, match="on_failure"):
        validate_graph(
            {"nodes": {"research": {"primitive": "research", "on_failure": "hope"}}, "edges": []}
        )


# ── engine: execution semantics (fakes only) ────────────────────────────────


def test_serial_turn_end_to_end(tmp_path):
    env = make_env(tmp_path, llm=ScriptedCriticLLM(["pass"]))
    answer, state = run_turn(env, SERIAL_GRAPH)
    assert answer == "FROZEN-ANSWER"
    assert state.status.value == "completed"
    events = state.metadata["graph_events"]
    assert [e["event"] for e in events].count("done") == 3
    # prompt committed its text; act/committing is merge's job only
    contents = [m.content for m in state.conversation.messages]
    assert any("concrete steps" in c for c in contents)
    assert env["store"].last_run("turn-graph")["status"] == "ok"


def test_retry_control_edge_rejects_then_recovers(tmp_path):
    env = make_env(tmp_path, llm=ScriptedCriticLLM(["fail", "pass"]))
    answer, state = run_turn(env, RETRY_GRAPH)
    assert env["executor"].calls == 2  # initial pass + one control re-run
    events = state.metadata["graph_events"]
    assert sum(e["event"] == "control" for e in events) == 1
    assert any("re-run from 'do'" in e.get("detail", "") for e in events if e["event"] == "control")
    assert state.status.value == "completed"


def test_retry_exhaustion_ends_the_turn(tmp_path):
    env = make_env(tmp_path, llm=ScriptedCriticLLM(["fail"]))
    answer, state = run_turn(env, RETRY_GRAPH)
    # initial + max_passes(2) re-runs, then the walk finishes (no infinite loop)
    assert env["executor"].calls == 3
    assert len(env["llm"].calls) == 3
    assert state.status.value == "completed"


def test_node_level_retry_policy(tmp_path):
    env = make_env(tmp_path, executor=FlakyExecutor(fail_times=1))
    doc = {
        "nodes": {"research": {"primitive": "research", "on_failure": "retry(1)"}},
        "edges": [],
    }
    answer, state = run_turn(env, doc)
    assert answer == "FROZEN-ANSWER"
    assert env["executor"].calls == 2
    events = [e["event"] for e in state.metadata["graph_events"]]
    assert events.count("failure") == 1
    assert "retry" in events


def test_node_level_skip_policy(tmp_path):
    env = make_env(tmp_path, executor=FlakyExecutor(fail_times=99))
    doc = {
        "nodes": {"research": {"primitive": "research", "on_failure": "skip"}},
        "edges": [],
    }
    answer, state = run_turn(env, doc)
    assert answer == ""  # nothing provided a result — the turn still ends
    assert env["executor"].calls == 1
    events = [e["event"] for e in state.metadata["graph_events"]]
    assert "skipped" in events
    assert state.status.value == "completed"


def test_fail_fast_is_the_default(tmp_path):
    env = make_env(tmp_path, executor=FlakyExecutor(fail_times=99))
    doc = {"nodes": {"research": {"primitive": "research"}}, "edges": []}
    env["store"].save("failing", doc)
    env["store"].set_active("failing")
    state = AgentState(agent_id="t", conversation=ConversationMemory(), goal=Goal(intent="g"))
    with pytest.raises(RuntimeError, match="fake executor exploded"):
        env["loop"].run(LoopComponents(executor=env["executor"]), state)
    assert state.status.value == "failed"
    assert env["store"].last_run("failing")["status"] == "failed"


def test_graph_budget_ceiling_is_binding(tmp_path):
    env = make_env(tmp_path)
    doc = {"budget": 1, "nodes": {"research": {"primitive": "research"}}, "edges": []}
    env["store"].save("tight", doc)
    env["store"].set_active("tight")
    state = AgentState(agent_id="t", conversation=ConversationMemory(), goal=Goal(intent="g"))
    with pytest.raises(GraphBudgetError):
        env["loop"].run(LoopComponents(executor=env["executor"]), state)
    assert state.status.value == "failed"
    assert env["store"].last_run("tight")["status"] == "failed"


def test_branch_budget_ceiling_is_binding(tmp_path):
    env = make_env(tmp_path)
    doc = {
        "nodes": {
            "fanout": {
                "primitive": "parallel",
                "branches": [
                    {
                        "name": "b1",
                        "budget": 1,  # below the research node's default allocation
                        "graph": {"nodes": {"gather": {"primitive": "research"}}, "edges": []},
                    }
                ],
            }
        },
        "edges": [],
    }
    validate_graph(doc)  # the ceiling is legal; it just cannot be served
    env["store"].save("tight-branch", doc)
    env["store"].set_active("tight-branch")
    state = AgentState(agent_id="t", conversation=ConversationMemory(), goal=Goal(intent="g"))
    with pytest.raises(GraphBudgetError):
        env["loop"].run(LoopComponents(executor=env["executor"]), state)


class TaskTextExecutor(FakeExecutor):
    """Answers with the task text itself — distinct per branch, so the
    determinism assertion pins a real ORDER, not a constant."""

    def execute(self, task, state):
        self.calls += 1
        self.tasks.append(task)
        return task.description


def test_parallel_determinism_across_five_runs(tmp_path):
    """FROZEN: same graph ⇒ identical conversation order across runs."""
    env = make_env(tmp_path, executor=TaskTextExecutor())
    signatures = []
    for i in range(5):
        _, state = run_turn(env, PARALLEL_GRAPH, agent_id=f"det-{i}")
        signatures.append(conversation_signature(state))
    assert all(s == signatures[0] for s in signatures)
    # goal first, then the TWO branch answers in DECLARED branch order (b1, b2)
    contents = [c for _, c in signatures[0]]
    joined = "\n".join(contents)
    assert joined.index("TASK-ONE") < joined.index("TASK-TWO")
    assert signatures[0][0][0] == "human"  # the goal banner


def test_parallel_branches_get_fresh_blackboards(tmp_path):
    """Branch commits never interleave into the shared conversation —
    only the declared merge node commits, serially, in declared order."""
    env = make_env(tmp_path, executor=FakeExecutor(answer=""))

    class CountingExecutor(FakeExecutor):
        def __init__(self):
            super().__init__(answer="")
            self.states = []

        def execute(self, task, state):
            self.states.append(state)
            self.calls += 1
            self.tasks.append(task)
            return f"ANS-{task.id}"

    counting = CountingExecutor()
    env["store"].save("iso", PARALLEL_GRAPH)
    env["store"].set_active("iso")
    state = AgentState(agent_id="t", conversation=ConversationMemory(), goal=Goal(intent="g"))
    env["loop"].run(LoopComponents(executor=counting), state)

    stores = {id(s) for s in counting.states}
    assert len(stores) == 2  # each branch ran on its OWN conversation store
    shared_store = id(state.conversation)
    assert shared_store not in stores
    contents = [m.content for m in state.conversation.messages]
    assert "ANS-gather1" in contents and "ANS-gather2" in contents
    assert contents.index("ANS-gather1") < contents.index("ANS-gather2")  # declared order


def test_branch_conversations_carry_the_goal_banner(tmp_path):
    """Branch parity: each branch conversation must be grounded like the
    ROOT's — [SystemMessage, goal banner, ...] in that order.

    Branch nodes reference "the goal" in their task text, and the executor
    only ever sees the branch conversation. Without the banner, a branch LLM
    improvises a goal of its own (observed live: a research branch answered
    about the project's codebase/memory instead of the user's actual
    question). Without the system prompt, it is also ungrounded in persona
    (no AGENTS.md). This pins BOTH so 'stay on task' holds for any graph."""
    env = make_env(tmp_path)

    class ConversationCapturingExecutor(FakeExecutor):
        def __init__(self):
            super().__init__(answer="ANS")
            self.conversations = []

        def execute(self, task, state):
            self.conversations.append(list(state.conversation.messages))
            return super().execute(task, state)

    capturing = ConversationCapturingExecutor()
    env["store"].save("grounded-branch", PARALLEL_GRAPH)
    env["store"].set_active("grounded-branch")
    state = AgentState(
        agent_id="t",
        conversation=ConversationMemory(),
        goal=Goal(intent="ontario taxes question"),
    )
    # The root is grounded with a system prompt (as AgentRuntime.initialize()
    # does). The branch must inherit it.
    state.initialize("ROOT-SYSTEM-PROTOCOL")
    env["loop"].run(LoopComponents(executor=capturing), state)

    assert len(capturing.conversations) == 2  # one per branch
    for messages in capturing.conversations:
        # 0) root persona/grounding, identical to the root's system message
        assert messages[0].type == "system"
        assert messages[0].content == "ROOT-SYSTEM-PROTOCOL"
        # 1) the turn's goal banner (branch nodes reference "the goal")
        assert messages[1].type == "human"
        assert "ontario taxes question" in messages[1].content


# ── branch failure containment (flaky branch ≠ failed turn) ─────────────────


class OneBranchFailExecutor(FakeExecutor):
    """Fails exactly the branch whose task text carries the poison marker —
    a flaky LLM endpoint (Ollama 500, a leaked CancelledError) while the
    sibling branch is still in flight. Surviving branches answer with their
    own task text, so committed content identifies the branch."""

    def __init__(self, poison="TASK-ONE", exc=None):
        super().__init__()
        self.poison = poison
        self.exc = exc or RuntimeError("branch LLM exploded (simulated)")

    def execute(self, task, state):
        self.calls += 1
        self.tasks.append(task)
        if self.poison in (task.description or ""):
            raise self.exc
        return task.description


def test_branch_runtime_failure_degrades_to_empty_slot(tmp_path):
    """PINNED: one flaky branch must NOT 500 the whole turn. The sibling
    finishes, the turn records `ok`, the failed branch lands in the merged
    list as a NULL slot (declared order preserved), and the failure is
    visible exactly once — a `branch_failed` event on the parallel node,
    tagged with the branch name (the canvas seam's record)."""
    env = make_env(tmp_path, executor=OneBranchFailExecutor())
    answer, state = run_turn(env, PARALLEL_GRAPH)

    assert state.status.value == "completed"
    assert env["store"].last_run("turn-graph")["status"] == "ok"

    events = state.metadata["graph_events"]
    bf = [e for e in events if e["event"] == "branch_failed"]
    assert len(bf) == 1
    assert bf[0]["node"] == "fanout"
    assert bf[0]["branch"] == "b1"
    assert "branch LLM exploded" in bf[0]["detail"]

    # The parallel node's merged slot is [None, <b2's answer>] — declared
    # order, with the degraded branch's slot explicit, not erased.
    fanout = [e for e in events if e["event"] == "result" and e["node"] == "fanout"]
    assert fanout[-1]["outputs"]["merged"] == [None, "TASK-TWO"]

    # The merge commits the survivor only — no failed-branch content leaks.
    contents = [m.content for m in state.conversation.messages]
    assert "TASK-TWO" in contents
    assert "TASK-ONE" not in contents


def test_all_branches_failed_still_completes(tmp_path):
    """PINNED (degenerate): every branch flaky → the turn still completes
    `ok` (degraded to an empty answer, like on_failure=skip) with one
    `branch_failed` event per branch — never a 500."""
    env = make_env(tmp_path, executor=OneBranchFailExecutor(poison="TASK"))
    answer, state = run_turn(env, PARALLEL_GRAPH)

    assert answer == ""
    assert state.status.value == "completed"
    assert env["store"].last_run("turn-graph")["status"] == "ok"
    bf = [e for e in state.metadata["graph_events"] if e["event"] == "branch_failed"]
    assert {e["branch"] for e in bf} == {"b1", "b2"}
    fanout = [e for e in state.metadata["graph_events"] if e["event"] == "result" and e["node"] == "fanout"]
    assert fanout[-1]["outputs"]["merged"] == [None, None]


def test_branch_cancelled_error_is_contained(tmp_path):
    """PINNED (the leak scenario): a branch raising asyncio.CancelledError —
    a BaseException that used to escape past `except Exception` and 500 the
    endpoint with no diagnostic — is a branch-level outcome, not a run-level
    one. The turn still completes `ok`; the event carries the type name."""
    import asyncio

    env = make_env(tmp_path, executor=OneBranchFailExecutor(exc=asyncio.CancelledError()))
    answer, state = run_turn(env, PARALLEL_GRAPH)

    assert state.status.value == "completed"
    assert env["store"].last_run("turn-graph")["status"] == "ok"
    bf = [e for e in state.metadata["graph_events"] if e["event"] == "branch_failed"]
    assert len(bf) == 1
    assert bf[0]["branch"] == "b1"
    assert "CancelledError" in bf[0]["detail"]


# ── live-run record (the API + canvas seam: the CURRENT node in flight) ────


def _start_background_run(env, graph_id, goal_id="test goal", executor=None):
    """Save+activate ``graph_id``'s stored doc and run one turn on a worker
    thread; returns the thread (join before the test exits)."""
    state = AgentState(
        agent_id="agent_live",
        conversation=ConversationMemory(),
        goal=Goal(intent=goal_id),
    )
    components = LoopComponents(executor=executor or env["executor"])
    errors = []

    def turn():
        try:
            env["loop"].run(components, state)
        except Exception as exc:  # surfaced at the join assertion
            errors.append(exc)

    t = threading.Thread(target=turn, name="live-run-test", daemon=True)
    t.start()
    return t, state, errors


def _wait_for_live(store, graph_id, predicate, timeout=5.0):
    """Poll the live record (bounded) until ``predicate(live)`` holds."""
    deadline = time.monotonic() + timeout
    while True:
        live = store.live_run(graph_id)
        if live is not None and predicate(live):
            return live
        assert time.monotonic() < deadline, "live run never reached the expected state"
        time.sleep(0.01)


LIVE_GRAPH = {
    "nodes": {
        "gather": {"primitive": "research", "config": {"task": "GATE-TASK"}},
        "answer": {"primitive": "act"},
    },
    "edges": [{"from": "gather", "to": "answer", "type": "data"}],
}


def test_live_run_exposes_current_node_mid_flight(tmp_path):
    """While a node is executing, the store's live record says
    status=running and carries that node's start event — the exact seam the
    /last-run endpoint and the designer canvas read. The finished record
    exists ONLY after the run ends, and the live record is cleared."""
    executor = GatedExecutor()
    env = make_env(tmp_path, executor=executor)
    env["store"].save("live-graph", LIVE_GRAPH, name="live graph")
    env["store"].set_active("live-graph")
    t, state, errors = _start_background_run(env, "live-graph", executor=executor)
    try:
        live = _wait_for_live(
            env["store"],
            "live-graph",
            lambda rec: any(e.get("event") == "start" and e.get("node") == "gather" for e in rec["events"]),
        )
        assert live["status"] == "running"
        assert live["events"][0]["event"] == "start"
        assert live["events"][0]["node"] == "gather"
        # The run is IN FLIGHT: the worker thread is parked inside 'gather';
        # the next node must not have started, and no finished record exists.
        assert not any(e.get("node") == "answer" for e in live["events"])
        assert env["store"].last_run("live-graph") is None
        executor.gate.set()  # release: the run completes on the worker thread
    finally:
        t.join(timeout=15.0)

    assert not errors
    assert env["store"].live_run("live-graph") is None  # live record cleared
    finished = env["store"].last_run("live-graph")
    assert finished["status"] == "ok"
    assert [e["event"] for e in finished["events"]].count("done") == 2
    assert finished["events"][0]["event"] == "start"
    assert finished["events"][0]["node"] == "gather"


def test_live_run_cleared_on_failure_and_record_keeps_events(tmp_path):
    """A failed run still mirrors events into the live record (the same
    seam), and both paths end with: finished record present, live cleared."""
    executor = GatedExecutor(fail_times=1)
    env = make_env(tmp_path, executor=executor)
    env["store"].save("live-fail", LIVE_GRAPH, name="live fail")
    env["store"].set_active("live-fail")
    t, state, errors = _start_background_run(env, "live-fail", executor=executor)
    try:
        live = _wait_for_live(
            env["store"],
            "live-fail",
            lambda rec: any(e.get("event") == "start" and e.get("node") == "gather" for e in rec["events"]),
        )
        assert live["status"] == "running"
        executor.gate.set()  # release: FakeExecutor(fail_times=1) now raises
    finally:
        t.join(timeout=15.0)

    assert errors, "the gated executor was supposed to fail the run"
    assert env["store"].live_run("live-fail") is None  # live cleared on failure too
    finished = env["store"].last_run("live-fail")
    assert finished["status"] == "failed"
    kinds = [(e["event"], e.get("node")) for e in finished["events"]]
    assert ("start", "gather") in kinds
    assert any(k[0] == "failure" and k[1] == "gather" for k in kinds)


def test_new_run_supersedes_stale_live_record(tmp_path):
    """begin_run for the same graph replaces any prior live record (e.g. a
    run superseded by the next turn) — append_event never resurrects it."""
    store = GraphStore(os.path.join(str(tmp_path), "supersede"))
    store.save("g", LIVE_GRAPH, name="supersede")
    store.begin_run("g")
    store.append_event("g", {"event": "start", "node": "gather", "at": 1})

    store.begin_run("g")  # same id, new run: the old event must be gone
    live = store.live_run("g")
    assert live["status"] == "running"
    assert live["events"] == []

    store.append_event("g", {"event": "start", "node": "answer", "at": 2})
    assert [e["node"] for e in store.live_run("g")["events"]] == ["answer"]


def test_append_event_noop_after_run_ends(tmp_path):
    """Late callbacks after clear_live must not resurrect the live record
    (event emission and run-end are on different locks by design)."""
    store = GraphStore(os.path.join(str(tmp_path), "late"))
    store.save("g", LIVE_GRAPH, name="late")
    store.begin_run("g")
    store.clear_live("g")
    store.append_event("g", {"event": "start", "node": "gather", "at": 3})
    assert store.live_run("g") is None


def test_no_registry_is_loud(tmp_path):
    loop = GraphLoop()
    state = AgentState(agent_id="t", conversation=ConversationMemory(), goal=Goal(intent="g"))
    with pytest.raises(GraphLoopError, match="ComponentRegistry"):
        loop.run(LoopComponents(executor=FakeExecutor()), state)


def test_no_active_graph_is_loud(tmp_path):
    env = make_env(tmp_path)  # store exists, but nothing saved/active
    state = AgentState(agent_id="t", conversation=ConversationMemory(), goal=Goal(intent="g"))
    with pytest.raises(GraphLoopError, match="no active graph"):
        env["loop"].run(LoopComponents(executor=env["executor"]), state)


# ── per-node outputs payload (result event: outputs + truncated) ───────────
#
# docs/ai-per-node-outputs.md: the result event carries the node's ACTUAL
# outputs, capped per EVENT, head+tail kept, cap-honest str() fallback.


RESEARCH_GRAPH = {
    "nodes": {"seed": {"primitive": "research", "config": {"task": "GATHER"}}},
    "edges": [],
}


def test_result_event_carries_outputs_payload(tmp_path):
    env = make_env(tmp_path)
    answer, state = run_turn(env, RESEARCH_GRAPH)
    assert answer == "FROZEN-ANSWER"
    result_events = [e for e in state.metadata["graph_events"] if e["event"] == "result"]
    assert len(result_events) == 1
    ev = result_events[0]
    # exact values, per typed output
    assert ev["outputs"]["result"] == "FROZEN-ANSWER"
    assert ev["outputs"]["context"] == "FROZEN-ANSWER"
    assert "truncated" not in ev  # small outputs: nothing cut
    # the seam is JSON-serializable (it becomes API record material)
    json.dumps(ev)
    # and the finished record (what GET /api/graphs/{id}/last-run serves) keeps it
    finished = env["store"].last_run("turn-graph")
    kept = [e for e in finished["events"] if e["event"] == "result"][0]
    assert kept["outputs"]["result"] == "FROZEN-ANSWER"


def test_long_string_keeps_head_and_tail(tmp_path):
    """FROZEN: an overlong string shrinks to head + tail — the CONCLUSION
    (the tail) must survive; front-only cuts are the bug this design fixes."""
    big = "H" * 19_990 + "CONCLUSION" + "T" * 20_000  # exactly 40 KB > 32 KB keep
    env = make_env(tmp_path, llm=FakeLLM(), executor=FakeExecutor(answer=big))
    answer, state = run_turn(env, RESEARCH_GRAPH)
    assert len(answer) == len(big)  # the blackboard value itself is never cut
    ev = [e for e in state.metadata["graph_events"] if e["event"] == "result"][0]
    view = ev["outputs"]["result"]
    assert view.startswith("H" * 100)           # head kept
    assert view.endswith("T" * 100)             # tail (conclusion) kept
    assert "CONCLUSION" not in view             # middle dropped
    assert "(dropped 7232 chars)" in view       # honest marker (40_000 - 32_768)
    assert ev["truncated"] == {"result": 40_000, "context": 40_000}
    json.dumps(ev)


class _HugeRepr:
    """A non-JSON object whose str() is far over the caps."""

    def __str__(self):
        return "R" * 500_000


def test_str_fallback_runs_through_the_caps():
    """FROZEN: the str() fallback CANNOT defeat the caps (a 500 KB object
    must not produce a 500 KB event)."""
    payload, truncated = serialize_node_outputs({"widget": _HugeRepr()})
    view = payload["widget"]
    assert view.startswith("R" * 100) and view.endswith("R" * 100)
    assert "dropped" in view
    assert truncated == {"widget": 500_000}
    # nested: the note lands on the TOP-LEVEL output key and reports the
    # original length (500 KB) of the text that was cut
    verdict = {"pass": True, "why": _HugeRepr(), "ok": False}
    payload2, truncated2 = serialize_node_outputs({"verdict": verdict})
    assert payload2["verdict"]["pass"] is True and payload2["verdict"]["ok"] is False
    assert "dropped" in payload2["verdict"]["why"]
    assert truncated2 == {"verdict": 500_000}


def test_event_total_cap_binds_and_budget_is_fresh_per_call():
    """FROZEN: the 128 KB budget is PER EVENT — total emitted never exceeds
    it, a key left with no budget gets a marker (not nothing), and a fresh
    call on the same data is equally fresh (no cumulative starvation)."""
    outputs = {f"k{i}": "x" * 40_000 for i in range(5)}
    payload, truncated = serialize_node_outputs(outputs)
    # each 40 KB string is shrunk (hence noted) …
    assert {"k0", "k1", "k2"} <= set(truncated)
    assert payload["k0"].startswith("xxx") and payload["k0"].endswith("xxx")
    # … and the event total is bounded; the last keys hit the budget
    assert sum(len(v) for v in payload.values()) <= EVENT_OUTPUT_CAP
    assert {"k3", "k4"} <= set(truncated)
    assert "exhausted" in payload["k4"]
    # fresh per call: same input, second serialization is equally fresh
    payload2, truncated2 = serialize_node_outputs(outputs)
    assert payload2 == payload and truncated2 == truncated


def test_control_rerun_events_each_carry_fresh_head_tail(tmp_path):
    """FROZEN: each control-edge re-run emits a FRESH result event with its
    own head+tail view — the per-event budget does not starve later passes."""
    big = "H" * 20_000 + "CONCLUSION" + "T" * 20_000
    env = make_env(
        tmp_path, llm=ScriptedCriticLLM(["fail", "pass"]), executor=FakeExecutor(answer=big)
    )
    answer, state = run_turn(env, RETRY_GRAPH)
    result_events = [
        e for e in state.metadata["graph_events"] if e["event"] == "result" and e.get("node") == "do"
    ]
    assert len(result_events) == 2
    for ev in result_events:
        assert ev["outputs"]["result"].startswith("H" * 100)
        assert ev["outputs"]["result"].endswith("T" * 100)
        assert ev["truncated"]["result"] == len(big)


def test_live_and_finished_records_keep_outputs_and_truncated(tmp_path):
    store = GraphStore(os.path.join(str(tmp_path), "outs"))
    store.save("g", RESEARCH_GRAPH, name="outs")
    store.begin_run("g")
    store.append_event(
        "g",
        {
            "event": "result",
            "node": "seed",
            "at": 1,
            "outputs": {"result": "R" * 10, "context": "C" * 10},
            "truncated": {"result": 999},
        },
    )
    live = store.live_run("g")
    assert live["events"][0]["outputs"]["result"] == "R" * 10
    assert live["events"][0]["truncated"] == {"result": 999}
    store.record_run("g", {"status": "ok", "answer": "a", "events": list(live["events"])})
    finished = store.last_run("g")
    assert finished["events"][0]["outputs"]["context"] == "C" * 10
    assert finished["events"][0]["truncated"] == {"result": 999}


# ── the registry door: primitives + graphs in describe() ────────────────────


# ── route node (v1 contract extension — docs/ai-routing-nodes.md) ────────────
#
# The 9th vocabulary entry: dynamic choice among wired branches. One
# decider (manual | llm), a choice set that must equal the outgoing data
# edges exactly, a mandatory reason in the outputs. Fakes pin the choice —
# the llm decider is the declared non-determinism.


ROUTE_GRAPH = {
    "nodes": {
        "dispatch": {
            "primitive": "route",
            "config": {
                "decider": "manual",
                "pinned": "web",
                "options": {"web": "Deep web research", "code": "Writes and tests code"},
                "fallback": "web",
            },
        },
        "web": {"primitive": "research", "config": {"task": "WEB-TASK"}},
        "code": {"primitive": "act"},
    },
    "edges": [
        {"from": "dispatch", "to": "web", "type": "data"},
        {"from": "dispatch", "to": "code", "type": "data"},
    ],
}


def _route_graph(decider="manual", pinned="web", fallback="web"):
    """A fresh ROUTE_GRAPH with the decider parts set the way the test expects."""
    doc = json.loads(json.dumps(ROUTE_GRAPH))
    cfg = doc["nodes"]["dispatch"]["config"]
    cfg["decider"] = decider
    if decider == "manual":
        cfg["pinned"] = pinned
    else:
        cfg.pop("pinned", None)
    if fallback is None:
        cfg.pop("fallback", None)
    else:
        cfg["fallback"] = fallback
    return doc


def _run_named(env, doc, name="route-graph"):
    env["store"].save(name, doc)
    env["store"].set_active(name)
    state = AgentState(
        agent_id="route-t", conversation=ConversationMemory(), goal=Goal(intent="test goal")
    )
    env["loop"].run(LoopComponents(executor=env["executor"]), state)
    return state


def _skipped(events):
    return [(e["node"], e.get("detail")) for e in events if e["event"] == "skipped"]


def _dispatch_result(events):
    return [e for e in events if e["event"] == "result" and e.get("node") == "dispatch"][0]


# ── validator: route wiring rules (save time — visible) ─────────────────────


def test_route_graph_validates():
    doc = validate_graph(ROUTE_GRAPH)
    assert doc.entry == "dispatch"
    assert doc.node("dispatch").config["decider"] == "manual"
    assert "route" in KNOWN_PRIMITIVES
    # the port shape: passthrough duality (research-style), no side effects
    port = PRIMITIVES["route"]
    assert port.accepts == ("goal", "context", "result")
    assert port.provides == ("context", "result")
    assert port.side_effects is False
    assert port.budgeted is True


def test_route_llm_config_validates():
    doc = _route_graph(decider="llm")
    assert validate_graph(doc).node("dispatch").config["decider"] == "llm"


def test_route_needs_at_least_two_outgoing_edges():
    doc = _route_graph()
    del doc["nodes"]["code"]
    doc["edges"] = [doc["edges"][0]]
    with pytest.raises(GraphError, match="at least two outgoing data edges"):
        validate_graph(doc)


def test_route_option_key_without_outgoing_edge_rejected():
    doc = _route_graph()
    doc["nodes"]["dispatch"]["config"]["options"]["ghost"] = "No edge for me"
    with pytest.raises(GraphError, match="no outgoing data edge"):
        validate_graph(doc)


def test_route_choice_set_missing_downstream_node_rejected():
    doc = _route_graph()
    del doc["nodes"]["dispatch"]["config"]["options"]["code"]
    with pytest.raises(GraphError, match="choice set is missing"):
        validate_graph(doc)


def test_route_decider_is_a_closed_enum():
    doc = _route_graph()
    cfg = doc["nodes"]["dispatch"]["config"]
    cfg["decider"] = "rule"
    with pytest.raises(GraphError, match="reserved"):
        validate_graph(doc)
    cfg["decider"] = "vibes"
    with pytest.raises(GraphError, match="'manual' or 'llm'"):
        validate_graph(doc)
    del cfg["decider"]
    with pytest.raises(GraphError, match="decider"):
        validate_graph(doc)


def test_route_manual_requires_valid_pinned():
    doc = _route_graph()
    cfg = doc["nodes"]["dispatch"]["config"]
    del cfg["pinned"]
    with pytest.raises(GraphError, match="pinned"):
        validate_graph(doc)
    cfg["pinned"] = "ghost"
    with pytest.raises(GraphError, match="not in the choice set"):
        validate_graph(doc)


def test_route_llm_rejects_pinned_and_checks_fallback_membership():
    doc = _route_graph(decider="llm")
    cfg = doc["nodes"]["dispatch"]["config"]
    cfg["pinned"] = "web"
    with pytest.raises(GraphError, match="only valid with the manual decider"):
        validate_graph(doc)
    del cfg["pinned"]
    with pytest.raises(GraphError, match="not in the choice set") as exc:
        cfg["fallback"] = "ghost"
        validate_graph(doc)
    assert "fallback" in str(exc.value)


def test_route_options_must_be_nonempty_descriptions():
    doc = _route_graph()
    doc["nodes"]["dispatch"]["config"]["options"]["web"] = 42
    with pytest.raises(GraphError, match="non-empty string"):
        validate_graph(doc)


def test_route_rules_hold_inside_a_parallel_branch():
    doc = {
        "nodes": {
            "fan": {
                "primitive": "parallel",
                "branches": [
                    {
                        "name": "b",
                        "graph": {
                            "nodes": {
                                "d": {"primitive": "route", "config": {"decider": "manual", "pinned": "r"}},
                                "r": {"primitive": "research"},
                            },
                            "edges": [{"from": "d", "to": "r"}],
                        },
                    }
                ],
            }
        },
        "edges": [],
    }
    with pytest.raises(GraphError, match="at least two outgoing data edges"):
        validate_graph(doc)


# ── engine: routing semantics (fakes only) ──────────────────────────────────


class ScriptedRouterLLM:
    """The route decider's LLM seam: a scripted choice sequence (fakes pin
    the decision — the declared non-determinism, never asserted on the harness)."""

    def __init__(self, choices):
        self.choices = list(choices)
        self.calls = 0

    def with_structured_output(self, schema):
        outer = self

        class _S:
            def invoke(self, messages):
                outer.calls += 1
                i = min(outer.calls - 1, len(outer.choices) - 1)
                return types.SimpleNamespace(choice=outer.choices[i], reason="scripted routing")

        return _S()


def test_route_manual_takes_pinned_branch_and_skips_the_rest(tmp_path):
    env = make_env(tmp_path)
    state = _run_named(env, ROUTE_GRAPH)
    assert state.status.value == "completed"
    # only the pinned branch ran; the act branch never touched the executor
    assert len(env["executor"].tasks) == 1
    assert "WEB-TASK" in env["executor"].tasks[0].description
    assert _skipped(state.metadata["graph_events"]) == [("code", "not routed")]
    # the decision is visible: choice + MANDATORY reason in the result event
    ev = _dispatch_result(state.metadata["graph_events"])
    assert ev["outputs"]["choice"] == "web"
    assert ev["outputs"]["reason"]
    # the payload passed through as BOTH context and result (research-style duality)
    assert ev["outputs"]["context"] is not None
    assert ev["outputs"]["result"] is not None
    json.dumps(ev)


def test_route_manual_can_pin_the_other_branch(tmp_path):
    env = make_env(tmp_path)
    state = _run_named(env, _route_graph(pinned="code"), name="pin-code")
    assert state.status.value == "completed"
    assert env["executor"].calls == 1  # the act branch ran
    assert _skipped(state.metadata["graph_events"]) == [("web", "not routed")]
    ev = _dispatch_result(state.metadata["graph_events"])
    assert ev["outputs"]["choice"] == "code"


def test_route_llm_decider_takes_the_chosen_branch(tmp_path):
    env = make_env(tmp_path, llm=ScriptedRouterLLM(["code"]))
    state = _run_named(env, _route_graph(decider="llm"), name="llm-code")
    assert env["llm"].calls == 1  # exactly one decision call
    assert env["executor"].calls == 1
    assert _skipped(state.metadata["graph_events"]) == [("web", "not routed")]
    ev = _dispatch_result(state.metadata["graph_events"])
    assert ev["outputs"]["choice"] == "code"
    assert ev["outputs"]["reason"] == "scripted routing"


def test_route_llm_invalid_choice_takes_fallback(tmp_path):
    env = make_env(tmp_path, llm=ScriptedRouterLLM(["ghost"]))
    state = _run_named(env, _route_graph(decider="llm"), name="llm-fb")
    events = state.metadata["graph_events"]
    ev = _dispatch_result(events)
    assert ev["outputs"]["choice"] == "web"  # the declared fallback
    assert "fallback" in ev["outputs"]["reason"].lower()
    assert _skipped(events) == [("code", "not routed")]
    assert state.status.value == "completed"


def test_route_llm_invalid_choice_without_fallback_fails_visibly(tmp_path):
    env = make_env(tmp_path, llm=ScriptedRouterLLM(["ghost"]))
    with pytest.raises(Exception, match="fallback"):
        _run_named(env, _route_graph(decider="llm", fallback=None), name="llm-nofb")
    assert env["store"].last_run("llm-nofb")["status"] == "failed"


def test_route_budget_zero_is_offline_fallback_without_llm_call(tmp_path):
    env = make_env(tmp_path, llm=ScriptedRouterLLM(["code"]))
    doc = _route_graph(decider="llm")
    doc["nodes"]["dispatch"]["budget"] = 0  # offline mode by construction
    state = _run_named(env, doc, name="off")
    assert env["llm"].calls == 0  # NO LLM call
    ev = _dispatch_result(state.metadata["graph_events"])
    assert ev["outputs"]["choice"] == "web"  # the declared fallback
    assert _skipped(state.metadata["graph_events"]) == [("code", "not routed")]
    assert state.status.value == "completed"


def test_route_budget_zero_without_fallback_fails(tmp_path):
    env = make_env(tmp_path, llm=ScriptedRouterLLM(["code"]))
    doc = _route_graph(decider="llm", fallback=None)
    doc["nodes"]["dispatch"]["budget"] = 0
    with pytest.raises(Exception, match="offline"):
        _run_named(env, doc, name="off-nofb")


def test_route_decision_costs_one_budget_unit(tmp_path):
    """FROZEN: the decision consumes exactly ONE executor-budget unit, so a
    ceiling of 2 serves decision(1) + chosen branch(1) but not a default node."""
    env = make_env(tmp_path, llm=ScriptedRouterLLM(["code"]))
    doc = _route_graph(decider="llm")
    doc["nodes"]["dispatch"]["budget"] = 1
    doc["nodes"]["code"]["budget"] = 1
    doc["budget"] = 2  # exactly covered: 1 (decision) + 1 (act)
    state = _run_named(env, doc, name="one-unit")
    assert state.status.value == "completed"
    assert env["executor"].calls == 1
    assert env["llm"].calls == 1


def test_route_ceiling_binds_after_the_decision(tmp_path):
    env = make_env(tmp_path, llm=ScriptedRouterLLM(["web"]))
    doc = _route_graph(decider="llm")
    doc["budget"] = 1  # the decision (1 unit) fits; research's default (6) does not
    env["store"].save("ceiling", doc)
    env["store"].set_active("ceiling")
    state = AgentState(agent_id="t", conversation=ConversationMemory(), goal=Goal(intent="g"))
    with pytest.raises(GraphBudgetError):
        env["loop"].run(LoopComponents(executor=env["executor"]), state)
    assert state.status.value == "failed"
    assert env["llm"].calls == 1  # the decision itself was served first
    assert env["executor"].calls == 0
    assert env["store"].last_run("ceiling")["status"] == "failed"


def test_route_transitive_subtree_skips_with_not_routed(tmp_path):
    doc = {
        "nodes": {
            "dispatch": {
                "primitive": "route",
                "config": {
                    "decider": "manual",
                    "pinned": "code",
                    "options": {"web": "Deep web research", "code": "Writes and tests code"},
                    "fallback": "code",
                },
            },
            "web": {"primitive": "research", "config": {"task": "WEB-TASK"}},
            "web-finish": {"primitive": "act", "config": {"task": "FINISH-WEB"}},
            "code": {"primitive": "act"},
        },
        "edges": [
            {"from": "dispatch", "to": "web", "type": "data"},
            {"from": "web", "to": "web-finish", "type": "data"},
            {"from": "dispatch", "to": "code", "type": "data"},
        ],
    }
    env = make_env(tmp_path)
    state = _run_named(env, doc, name="transitive")
    assert _skipped(state.metadata["graph_events"]) == [
        ("web", "not routed"),
        ("web-finish", "not routed"),
    ]
    # the chosen branch ran; the unchosen subtree never touched the executor
    assert [t.description for t in env["executor"].tasks]
    assert state.status.value == "completed"


ROUTE_MERGE_GRAPH = {
    "nodes": {
        "dispatch": {
            "primitive": "route",
            "config": {
                "decider": "manual",
                "pinned": "web",
                "options": {"web": "Deep web research", "code": "Writes and tests code"},
                "fallback": "web",
            },
        },
        "web": {"primitive": "research", "config": {"task": "WEB-TASK"}},
        "code": {"primitive": "research", "config": {"task": "CODE-TASK"}},
        "join": {"primitive": "merge"},
    },
    "edges": [
        {"from": "dispatch", "to": "web", "type": "data"},
        {"from": "dispatch", "to": "code", "type": "data"},
        {"from": "web", "to": "join", "type": "data"},
        {"from": "code", "to": "join", "type": "data"},
    ],
}


def test_route_convergence_merge_commits_only_active_inputs(tmp_path):
    """FROZEN: the pruned branch is a declared alternative, not an error — the
    re-joined merge commits ONLY what ran."""
    env = make_env(tmp_path, executor=TaskTextExecutor())
    state = _run_named(env, ROUTE_MERGE_GRAPH, name="conv")
    contents = [m.content for m in state.conversation.messages]
    assert any("WEB-TASK" in c for c in contents)
    assert not any("CODE-TASK" in c for c in contents)
    assert _skipped(state.metadata["graph_events"]) == [("code", "not routed")]
    assert state.status.value == "completed"


class PlanRequirer(NodeBase):
    """Test-only primitive: requires 'plan'. Exercises the convergence rule
    (a node whose required input has no ACTIVE provider at the re-join)."""

    name = "needs-plan"
    provides = ("result",)
    accepts = ("context", "plan")
    requires = ("plan",)
    description = "test only: requires the 'plan' input"

    def run(self, state, config):
        state.out("result", "PLAN-USE")


class FakePlan(NodeBase):
    """Test-only primitive: emits a task list without a planner slot."""

    name = "fake-plan"
    provides = ("plan",)
    accepts = ("context", "goal")
    description = "test only: emits one task"

    def run(self, state, config):
        from tasks.task import Task

        state.out("plan", [Task(id=f"{state.node_id}-1", description="FAKE-PLAN-TASK", budget=1)])


def _needs_plan_env(tmp_path, monkeypatch):
    from schemas import graph as graph_schema

    monkeypatch.setitem(
        graph_schema.PRIMITIVES, "needs-plan",
        PortShape(requires=("plan",), accepts=("context", "plan"), provides=("result",)),
    )
    monkeypatch.setitem(
        graph_schema.PRIMITIVES, "fake-plan",
        PortShape(provides=("plan",), accepts=("context", "goal")),
    )
    env = make_env(tmp_path)
    env["registry"].register_primitive("needs-plan", PlanRequirer, version="0.0.0", description="test only")
    env["registry"].register_primitive("fake-plan", FakePlan, version="0.0.0", description="test only")
    return env


def _route_join_needs_plan(pinned):
    return {
        "nodes": {
            "dispatch": {
                "primitive": "route",
                "config": {
                    "decider": "manual",
                    "pinned": pinned,
                    "options": {"web": "Gathers context", "planner": "Plans the work"},
                    "fallback": pinned,
                },
            },
            "web": {"primitive": "research", "config": {"task": "WEB-TASK"}},
            "planner": {"primitive": "fake-plan"},
            "needsplan": {"primitive": "needs-plan"},
        },
        "edges": [
            {"from": "dispatch", "to": "web", "type": "data"},
            {"from": "dispatch", "to": "planner", "type": "data"},
            {"from": "web", "to": "needsplan", "type": "data"},
            {"from": "planner", "to": "needsplan", "type": "data"},
        ],
    }


def test_route_upstream_pruned_is_skipped_not_failed(tmp_path, monkeypatch):
    """FROZEN: at a re-join, a required input with no ACTIVE provider is a
    clean skip ('upstream pruned') — never a failure."""
    env = _needs_plan_env(tmp_path, monkeypatch)
    state = _run_named(env, _route_join_needs_plan("web"), name="up-prune")
    events = state.metadata["graph_events"]
    assert ("planner", "not routed") in _skipped(events)
    assert ("needsplan", "upstream pruned") in _skipped(events)
    assert not any(e["event"] in ("failure", "aborted") for e in events)
    assert state.status.value == "completed"


def test_route_upstream_pruned_flips_when_the_provider_branch_is_selected(tmp_path, monkeypatch):
    env = _needs_plan_env(tmp_path, monkeypatch)
    state = _run_named(env, _route_join_needs_plan("planner"), name="up-runs")
    events = state.metadata["graph_events"]
    assert _skipped(events) == [("web", "not routed")]
    ev = [e for e in events if e["event"] == "result" and e.get("node") == "needsplan"][0]
    assert ev["outputs"]["result"] == "PLAN-USE"
    assert state.status.value == "completed"


def test_route_determinism_across_five_runs(tmp_path):
    """FROZEN: given the decider's decision (fakes pin it), the run is
    deterministic — same conversation order across 5 runs."""
    env = make_env(tmp_path, llm=ScriptedRouterLLM(["code"]), executor=TaskTextExecutor())
    doc = json.loads(json.dumps(ROUTE_MERGE_GRAPH))
    cfg = doc["nodes"]["dispatch"]["config"]
    cfg["decider"] = "llm"
    cfg.pop("pinned", None)
    signatures = []
    for i in range(5):
        env["store"].save("det", doc)
        env["store"].set_active("det")
        state = AgentState(
            agent_id=f"route-det-{i}", conversation=ConversationMemory(), goal=Goal(intent="g")
        )
        env["loop"].run(LoopComponents(executor=env["executor"]), state)
        signatures.append(conversation_signature(state))
    assert all(s == signatures[0] for s in signatures)


def test_describe_exposes_primitive_vocabulary_and_graphs(tmp_path):
    env = make_env(tmp_path)
    env["store"].save("g1", SERIAL_GRAPH, name="serial")
    env["store"].set_active("g1")
    describe = env["registry"].describe()
    names = {p["name"] for p in describe["primitives"]}
    assert names == {"prompt", "research", "plan", "act", "critic", "gate", "merge", "parallel", "route"}
    act = next(p for p in describe["primitives"] if p["name"] == "act")
    assert act["side_effects"] is True
    assert act["provides"] == ["result"]
    route = next(p for p in describe["primitives"] if p["name"] == "route")
    assert route["provides"] == ["context", "result"]
    assert "side_effects" not in route
    assert describe["graphs"]["active"] == "g1"
    assert describe["graphs"]["graphs"][0]["name"] == "serial"


def test_primitive_instance_is_cached_and_swappable(tmp_path):
    env = make_env(tmp_path)
    first = env["registry"].primitive("prompt")
    assert first is env["registry"].primitive("prompt")

    class MyPromptNode(type(first)):
        pass

    env["registry"].register_primitive("prompt", MyPromptNode)
    assert type(env["registry"].primitive("prompt")) is MyPromptNode  # re-registration swaps
