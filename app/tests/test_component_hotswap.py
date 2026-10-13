"""Tests for the hot-swappable segregation of the agent loop.

Pinned behaviors:
  * ComponentRegistry: registration, activation, idempotency, presets
    (atomic), per-agent memory instances, generation counters, immutability
    of the StaticRegistry back-compat path.
  * Loops: PlanExecuteLoop preserves the classic plan/execute/evaluate
    topology including replan; DirectLoop never plans.
  * Hot-swap: activating a new alias/preset changes the NEXT turn's
    behavior on the same live AgentRuntime (REPL semantics); an in-flight
    turn finishes on the component set it started with (snapshot semantics).
  * Memory slot: per-agent stores, live rebind on swap, stateless
    replace()-after-swap safety.
  * API: GET /api/components, POST /api/components/activate contract.
  * Manifests: JSON manifests register slots + presets declaratively.

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage

from agent import AgentRuntime
from components import (
    ComponentContext,
    ComponentRegistry,
    ComponentSlot,
    apply_manifest,
    register_builtin_components,
)
from components.loops import DirectLoop, LoopComponents, PlanExecuteLoop
from components.registry import (
    ComponentAliasError,
    StaticRegistry,
)
from goals.goal import Goal
from memory import ConversationMemory, TrimmedMemory
from state import AgentState
from tasks.task import Task
from evaluator import AlwaysCompleteEvaluator
from tools.tool_registry import ToolRegistry


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeStructured:
    """Structured-output stand-in: always yields a valid Goal."""

    def invoke(self, messages):
        return Goal(intent="fake intent", requirements=["fake requirement"])


class FakeLLM:
    """Just enough of the LLMProvider surface for construction + no-op calls."""

    def with_structured_output(self, schema):
        return FakeStructured()

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="fake-llm-answer")


class RecordingPlanner:
    def __init__(self):
        self.calls = 0

    def create_plan(self, state):
        self.calls += 1
        goal_text = getattr(state.goal, "text", None) or str(state.goal)
        return [Task(id="t1", description=goal_text, success_criteria="done")]


class TwoTaskPlanner:
    def __init__(self):
        self.calls = 0

    def create_plan(self, state):
        self.calls += 1
        return [
            Task(id="a", description="first", success_criteria="x"),
            Task(id="b", description="second", success_criteria="y"),
        ]


class RecordingExecutor:
    def __init__(self, answer="RECORDED"):
        self.answer = answer
        self.ran = []
        self.hook = None  # optional callable(state) invoked mid-execution

    def execute(self, task, state):
        self.ran.append(task.description)
        if self.hook:
            self.hook(state)
        return self.answer


def make_registry(tmp_path, **context_kwargs):
    context = ComponentContext(
        llm=FakeLLM(), tools=ToolRegistry(), config=None, **context_kwargs
    )
    registry = ComponentRegistry(context)
    register_builtin_components(registry, context)
    return registry


def make_runtime(registry, tmp_path, agent_id="agent_under_test"):
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text("You are a test harness.")
    store = registry.create(ComponentSlot.MEMORY)
    state = AgentState(agent_id=agent_id, goal=None, conversation=store)

    class Cfg:
        agent_path = str(agents_md)
        max_iterations = 5

    return AgentRuntime(state=state, config=Cfg(), registry=registry)


# ── Registry unit tests ──────────────────────────────────────────────────────


def test_first_registered_alias_becomes_active(tmp_path):
    registry = make_registry(tmp_path)
    assert registry.active_alias(ComponentSlot.LOOP) in ("plan_execute", "direct")
    # built-in registration pins the documented defaults
    assert registry.active_alias(ComponentSlot.LOOP) == "plan_execute"
    assert registry.active_alias(ComponentSlot.PLANNER) == "default"


def test_activate_is_idempotent_for_current_alias(tmp_path):
    registry = make_registry(tmp_path)
    first = registry.active(ComponentSlot.LOOP)
    again = registry.activate(ComponentSlot.LOOP, "plan_execute")
    assert first is again


def test_activate_swaps_instance(tmp_path):
    registry = make_registry(tmp_path)
    before = registry.active(ComponentSlot.LOOP)
    after = registry.activate(ComponentSlot.LOOP, "direct")
    assert before is not after
    assert isinstance(before, PlanExecuteLoop)
    assert isinstance(after, DirectLoop)


def test_unknown_alias_raises(tmp_path):
    registry = make_registry(tmp_path)
    with pytest.raises(ComponentAliasError):
        registry.activate(ComponentSlot.PLANNER, "nope")


def test_unknown_preset_raises(tmp_path):
    registry = make_registry(tmp_path)
    with pytest.raises(ComponentAliasError):
        registry.activate_preset("nope")


def test_preset_activation_is_atomic(tmp_path):
    registry = make_registry(tmp_path)

    def boom(ctx):
        raise RuntimeError("factory failure")

    registry.register(ComponentSlot.PLANNER, "boom", boom)

    class BoomExecutor:
        def execute(self, task, state):
            return "boom-answer"

    registry.register(ComponentSlot.EXECUTOR, "boom", lambda ctx: BoomExecutor())
    registry.register_preset("partial", {
        ComponentSlot.PLANNER: "boom",
        ComponentSlot.EXECUTOR: "boom",
    })

    planner_before = registry.active(ComponentSlot.PLANNER)
    executor_before = registry.active(ComponentSlot.EXECUTOR)
    with pytest.raises(RuntimeError, match="factory failure"):
        registry.activate_preset("partial")
    # Nothing moved.
    assert registry.active(ComponentSlot.PLANNER) is planner_before
    assert registry.active(ComponentSlot.EXECUTOR) is executor_before


def test_create_is_per_agent_for_memory(tmp_path):
    registry = make_registry(tmp_path)
    a = registry.create(ComponentSlot.MEMORY)
    b = registry.create(ComponentSlot.MEMORY)
    assert isinstance(a, ConversationMemory)
    assert a is not b


def test_generation_bumps_on_activation(tmp_path):
    registry = make_registry(tmp_path)
    g0 = registry.generation(ComponentSlot.LOOP)
    registry.activate(ComponentSlot.LOOP, "direct")
    g1 = registry.generation(ComponentSlot.LOOP)
    registry.activate(ComponentSlot.LOOP, "direct")  # idempotent: no bump
    assert g1 > g0
    assert registry.generation(ComponentSlot.LOOP) == g1


def test_describe_shape(tmp_path):
    registry = make_registry(tmp_path)
    describe = registry.describe()
    # "primitives" is the graph-loop door added in v0 (always present; empty
    # until register_graph_components runs). "graphs" appears once a store
    # view is wired (main.py / register_graph_components).
    assert set(describe) == {"slots", "presets", "primitives"}
    assert describe["primitives"] == []
    assert set(describe["slots"]) == {s.value for s in ComponentSlot}
    entry = describe["slots"]["executor"]
    assert entry["active"] in {a["alias"] for a in entry["available"]}
    assert "default" in describe["presets"]


def test_static_registry_is_immutable(tmp_path):
    registry = make_registry(tmp_path)
    frozen = StaticRegistry({ComponentSlot.LOOP: PlanExecuteLoop()})
    assert frozen.active(ComponentSlot.LOOP) is not None
    with pytest.raises(RuntimeError, match="immutable"):
        frozen.activate(ComponentSlot.LOOP, "direct")
    with pytest.raises(RuntimeError, match="immutable"):
        frozen.activate_preset("default")


# ── Loop topology tests ──────────────────────────────────────────────────────


def _state(tmp_path, goal_text="goal"):
    store = ConversationMemory()
    state = AgentState(agent_id="loop-test", goal=Goal(intent=goal_text), conversation=store)
    return state


def test_plan_execute_loop_replans_after_failure(tmp_path):
    planner = TwoTaskPlanner()
    executor = RecordingExecutor()
    replans = {"count": 0}

    class ReplanOnceEvaluator:
        def evaluate(self, state, task):
            from evaluator import EvaluationAction, EvaluationResult
            if replans["count"] < 1:
                replans["count"] += 1
                return EvaluationResult(action=EvaluationAction.REPLAN, reason="not yet")
            return EvaluationResult(action=EvaluationAction.CONTINUE, reason="next")

    components = LoopComponents(planner=planner, executor=executor,
                                evaluator=ReplanOnceEvaluator())
    state = _state(tmp_path)
    result = PlanExecuteLoop().run(components, state)
    assert planner.calls == 2  # replanned after the first task
    # The replan restarts at the plan head: first, first, second.
    assert executor.ran == ["first", "first", "second"]
    assert result == "RECORDED"


def test_direct_loop_never_calls_planner(tmp_path):
    planner = RecordingPlanner()
    components = LoopComponents(loop=DirectLoop(), planner=planner,
                                executor=RecordingExecutor(answer="DIRECT"),
                                evaluator=AlwaysCompleteEvaluator())
    state = _state(tmp_path, goal_text="the goal text")
    result = DirectLoop().run(components, state)
    assert planner.calls == 0
    assert result == "DIRECT"


# ── Hot-swap mid-conversation (the core property) ───────────────────────────


def test_executor_swap_changes_next_turn_only(tmp_path):
    registry = make_registry(tmp_path)

    class NoopExecutor:
        def execute(self, task, state):
            return "NOOP-ANSWER"

    registry.register(ComponentSlot.EXECUTOR, "noop", lambda ctx: NoopExecutor())
    registry.activate_preset("offline")  # echo executor active

    agent = make_runtime(registry, tmp_path)
    turn1 = agent.chat("hello", history=[HumanMessage(content="prior")])
    assert str(turn1).startswith("ECHO[direct]:")

    registry.activate(ComponentSlot.EXECUTOR, "noop")

    turn2 = agent.chat("hello again", history=[HumanMessage(content="prior")])
    assert turn2 == "NOOP-ANSWER"

    # And the swap is sticky: the next preset activation takes over again.
    registry.activate_preset("offline")
    turn3 = agent.chat("third", history=[HumanMessage(content="prior")])
    assert str(turn3).startswith("ECHO[direct]:")


def test_swap_mid_turn_does_not_affect_in_flight_turn(tmp_path):
    registry = make_registry(tmp_path)

    sneaky = RecordingExecutor(answer="SNIP")

    def swap_loop_mid_turn(state):
        registry.activate(ComponentSlot.LOOP, "plan_execute")

    sneaky.hook = swap_loop_mid_turn
    registry.register(ComponentSlot.EXECUTOR, "sneaky", lambda ctx: sneaky)
    registry.activate_preset("offline")
    registry.activate(ComponentSlot.EXECUTOR, "sneaky")
    agent = make_runtime(registry, tmp_path)

    turn1 = agent.chat("mid-turn swap", history=[HumanMessage(content="prior")])
    assert turn1 == "SNIP"  # finished on the direct loop it started with

    # Next turn uses the planner (plan_execute active now).
    planner = RecordingPlanner()
    registry.register(ComponentSlot.PLANNER, "recorder", lambda ctx: planner)
    registry.activate(ComponentSlot.PLANNER, "recorder")
    agent.chat("second turn", history=[HumanMessage(content="prior")])
    assert planner.calls == 1


def test_loop_swap_changes_topology(tmp_path):
    registry = make_registry(tmp_path)
    registry.activate_preset("offline")  # direct loop
    agent = make_runtime(registry, tmp_path)

    planner = RecordingPlanner()
    registry.register(ComponentSlot.PLANNER, "recorder", lambda ctx: planner)
    registry.activate_preset("default")
    registry.activate(ComponentSlot.PLANNER, "recorder")

    # plan_execute active: the planner is consulted even with a trivial task.
    agent.chat("topology check", history=[HumanMessage(content="prior")])
    assert planner.calls == 1


def test_memory_swap_rebinds_live_store(tmp_path):
    registry = make_registry(tmp_path, extras={"memory_max_messages": 2})
    registry.activate_preset("offline")
    agent = make_runtime(registry, tmp_path)
    assert isinstance(agent.state.conversation, ConversationMemory)

    # Accumulate some session content first.
    agent.chat("warmup", history=[HumanMessage(content="warmup-content")])
    joined_before = " ".join(
        m.content for m in agent.state.conversation.get() if isinstance(m.content, str)
    )
    assert "warmup-content" in joined_before

    registry.activate(ComponentSlot.MEMORY, "trimmed")

    # Next turn rebinds to a fresh TrimmedMemory (system prompt re-applied).
    agent.chat("after memory swap", history=[HumanMessage(content="prior")])
    store = agent.state.conversation
    assert isinstance(store, TrimmedMemory)
    # Old REPL history did not leak into the new store.
    contents = [m.content for m in store.get() if isinstance(m.content, str)]
    assert "warmup-content" not in contents


def test_stateless_replace_survives_memory_swap(tmp_path):
    registry = make_registry(tmp_path)
    registry.activate_preset("offline")
    agent = make_runtime(registry, tmp_path)
    registry.activate(ComponentSlot.MEMORY, "trimmed")

    answer = agent.chat("fresh", history=[HumanMessage(content="client-thread")])
    contents = [m.content for m in agent.state.conversation.get()
                if isinstance(m.content, str)]
    assert "client-thread" in contents  # replace() landed in the LIVE store
    assert str(answer).startswith("ECHO[")


# ── API contract ─────────────────────────────────────────────────────────────


@pytest.fixture()
def components_api(tmp_path, monkeypatch):
    import server
    registry = make_registry(tmp_path, extras={"memory_max_messages": 3})
    registry.activate_preset("offline")
    monkeypatch.setattr(server, "get_registry", lambda: registry)
    client = TestClient(server.app)
    return client, registry


def test_components_status_endpoint(components_api):
    client, registry = components_api
    res = client.get("/api/components")
    assert res.status_code == 200
    body = res.json()
    assert body["slots"]["executor"]["active"] == "echo"
    assert "offline" in body["presets"]
    aliases = {a["alias"] for a in body["slots"]["loop"]["available"]}
    assert aliases == {"plan_execute", "direct"}


def test_activate_preset_endpoint(components_api):
    client, registry = components_api
    res = client.post("/api/components/activate", json={"preset": "fast"})
    assert res.status_code == 200
    assert res.json()["slots"]["planner"]["active"] == "trivial"
    assert res.json()["slots"]["memory"]["active"] == "trimmed"


def test_activate_single_slot_endpoint(components_api):
    client, registry = components_api
    res = client.post("/api/components/activate",
                      json={"slot": "loop", "alias": "direct"})
    assert res.status_code == 200
    assert res.json()["slots"]["loop"]["active"] == "direct"


def test_activate_rejects_both_preset_and_slot(components_api):
    client, _ = components_api
    res = client.post("/api/components/activate",
                      json={"preset": "fast", "slot": "loop", "alias": "direct"})
    assert res.status_code == 400


def test_activate_rejects_unknown_alias(components_api):
    client, _ = components_api
    res = client.post("/api/components/activate",
                      json={"slot": "loop", "alias": "nope"})
    assert res.status_code == 400


def test_activate_rejects_unknown_slot(components_api):
    client, _ = components_api
    res = client.post("/api/components/activate",
                      json={"slot": "nope", "alias": "direct"})
    assert res.status_code == 400


def test_server_chat_still_works_after_activation(components_api, monkeypatch, tmp_path):
    import server
    agent = make_runtime(components_api[1], tmp_path, agent_id="swap-chat")
    monkeypatch.setattr(server, "build_agent", lambda *a, **kw: agent)
    client, _ = components_api
    res = client.post("/api/chat", json={"message": "ping"})
    assert res.status_code == 200


# ── Manifests ────────────────────────────────────────────────────────────────


def test_apply_manifest_registers_and_activates(tmp_path):
    registry = make_registry(tmp_path)
    manifest = {
        "slots": {
            "executor": {
                "marker": {
                    "factory": "tests_manifest_helpers.MarkerExecutor",
                    "version": "9.9.9",
                    "description": "marker",
                }
            }
        },
        "presets": {"marked": {"executor": "marker"}},
    }
    # Provide the factory module dynamically.
    import types
    module = types.ModuleType("tests_manifest_helpers")

    class MarkerExecutor:
        def execute(self, task, state):
            return "MARKER"

    module.MarkerExecutor = MarkerExecutor
    sys.modules["tests_manifest_helpers"] = module

    summary = apply_manifest(registry, manifest)
    assert summary["slots"]["executor"] == ["marker"]
    assert "marked" in summary["presets"]

    registry.activate_preset("marked")
    assert isinstance(registry.active(ComponentSlot.EXECUTOR), MarkerExecutor)
    aliases = registry.aliases(ComponentSlot.EXECUTOR)
    assert "marker" in aliases


def test_manifest_missing_factory_fails(tmp_path):
    registry = make_registry(tmp_path)
    from components import ManifestError
    with pytest.raises(ManifestError):
        apply_manifest(registry, {"slots": {"loop": {"bad": {"version": "1"}}}})


def test_manifest_unknown_slot_fails(tmp_path):
    registry = make_registry(tmp_path)
    from components import ManifestError
    with pytest.raises(ManifestError):
        apply_manifest(registry, {"presets": {"x": {"slot_not_here": "default"}}})
