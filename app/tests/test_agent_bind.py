"""Bind-then-run tests (v1, docs/agent-graph.md — steps 4–5 of the build order).

Pinned behaviors (the doc's "Bind then run" + "Skills" + "Pool" groups),
fakes only (the repo's frozen rule):

  * loop: ``ref`` flips the loop slot to ``graph`` + the graph store's
    active id; ``alias`` activates the built-in loop alias;
  * settings: one slot alias per entry (``memory: trimmed``) — an unknown
    alias is a VISIBLE bind failure, not a silent default;
  * model: a fresh provider lands on ``context.llm`` and the LLM-capturing
    slots are REBUILT against it (registry.rebuild — a plain re-activate
    would keep the stale instance); a provider construction error is visible;
  * mcp: pooled connections + the remote tools merged into the context's
    ToolRegistry as ordinary entries — the executor's
    ToolRegistry.execute path calls the remote tool with zero special-casing;
    an unreachable server is a visible bind failure; an alias a new document
    no longer declares loses its stale adapters;
  * skills: the fragment lands in the composed persona (prompt REPLACES the
    AGENTS.md default, skill text rides alongside); a skill's declared tools
    must resolve at bind time (a missing one is visible);
  * persona: materialized to a file + ``agent_path`` pointed at it — the
    read path (AgentRuntime) is untouched;
  * the lazy hook (ensure_agent_bound): the signature check makes a
    no-op re-bind a no-op, and deactivation restores the default persona;
  * detach (the delete path): ``bind_and_remember`` records the pre-bind
    shared-state snapshot, and ``detach_agent`` (only when THAT agent is the
    bound one) rolls loop slot + active graph, model, MCP adapters, settings
    and persona back — a deleted agent never keeps running the next turn.

Run from the ``app/`` directory:
    PYTHONPATH=.pydeps python3 -m pytest tests/test_agent_bind.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from components import (
    AgentBindError,
    ComponentContext,
    ComponentRegistry,
    ComponentSlot,
    bind_agent,
    bind_and_remember,
    deactivate_agent,
    detach_agent,
    ensure_agent_bound,
    register_builtin_components,
    register_graph_components,
)
from components.agent_store import AgentStore
from components.graph_store import GraphStore
from mcp import McpPool
from mcp.client import McpTool
from tools.tool_registry import ToolRegistry
from agent_config import AgentConfig


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeStructured:
    def invoke(self, messages):
        from goals.goal import Goal

        return Goal(intent="fake intent")


class FakeModel:
    def __init__(self, model_id):
        self.model_id = model_id

    def with_tools(self, tools):
        return self

    def with_structured_output(self, schema):
        return FakeStructured()


class FakeMcpConnection:
    """The McpConnection contract with counters (see test_mcp.py)."""

    def __init__(self, spec):
        self.spec = spec
        self.closed = False
        self.calls = []

    @property
    def live(self):
        return not self.closed

    connected = live

    def connect(self):
        return self

    def close(self):
        self.closed = True

    def list_tools(self):
        return [McpTool("echo", "echo its args", None)]

    def call_tool(self, name, args=None):
        import json

        self.calls.append((name, args))
        return f"fake:{name}:{json.dumps(args or {})}"


class FakeUnreachable:
    """A factory that always fails — the visible-bind-failure pin.

    Raises :class:`McpError` (the real client's failure type for an
    unreachable server), so the catch in ``bind_agent`` is the honest path."""

    def __init__(self, message="fake MCP server refused to connect"):
        self.message = message

    def __call__(self, spec):
        from mcp import McpError

        raise McpError(self.message)


_HELLO_GRAPH = {
    "name": "hello",
    "budget": 15,
    "nodes": {
        "frame": {"primitive": "prompt", "config": {"text": "Be concise."}},
        "answer": {"primitive": "act", "config": {"task": "Answer the goal."}},
    },
    "edges": [{"from": "frame", "to": "answer", "type": "data"}],
}


@pytest.fixture()
def harness(tmp_path):
    """registry + context + stores, all temp-dir fakes (fakes-only rule)."""
    graphs = GraphStore(os.path.join(str(tmp_path), "bind-graphs"))
    graphs.save("killer-research", _HELLO_GRAPH, name="killer")
    agents = AgentStore(
        os.path.join(str(tmp_path), "bind-agents"),
        graph_ids=lambda: graphs.ids(),
        skill_root=str(tmp_path),
    )
    (tmp_path / "citation.md").write_text("# Cite your sources\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("Default persona.\n", encoding="utf-8")

    tools = ToolRegistry()
    context = ComponentContext(
        llm=FakeModel("default"), tools=tools,
        config=AgentConfig(agent_path=str(tmp_path / "AGENTS.md")),
    )
    registry = ComponentRegistry(context)
    register_builtin_components(registry, context)
    register_graph_components(registry, context, graph_store=graphs)

    def fake_pool():
        return McpPool(factory=lambda spec: FakeMcpConnection(spec))

    bound = {}
    try:
        yield {
            "registry": registry,
            "context": context,
            "tools": tools,
            "graphs": graphs,
            "agents": agents,
            "pool": fake_pool,
            "root": str(tmp_path),
            "bound": bound,
        }
    finally:
        pass


# ── loop ─────────────────────────────────────────────────────────────────────


def test_loop_ref_activates_graph_slot_and_active_graph(harness):
    registry, graphs = harness["registry"], harness["graphs"]
    doc = {"loop": {"ref": "killer-research"}}
    bound = bind_agent(registry, doc, graph_store=graphs)
    assert bound["loop"] == {"ref": "killer-research"}
    assert registry.active_alias(ComponentSlot.LOOP) == "graph"
    assert graphs.active_id == "killer-research"


def test_loop_alias_activates_builtin_loop(harness):
    registry = harness["registry"]
    doc = {"loop": {"alias": "direct"}}
    bound = bind_agent(registry, doc)
    assert bound["loop"] == {"alias": "direct"}
    assert registry.active_alias(ComponentSlot.LOOP) == "direct"


def test_loop_unknown_ref_is_visible(harness):
    registry = harness["registry"]
    with pytest.raises(AgentBindError, match="bind loop"):
        bind_agent(registry, {"loop": {"ref": "no-such-graph"}})


def test_loop_missing_is_visible(harness):
    registry = harness["registry"]
    with pytest.raises(AgentBindError, match="'loop' is required"):
        bind_agent(registry, {})


# ── settings ─────────────────────────────────────────────────────────────────


def test_settings_swap_slot_aliases(harness):
    registry = harness["registry"]
    doc = {"loop": {"alias": "direct"}, "settings": {"memory": "trimmed"}}
    bound = bind_agent(registry, doc)
    assert bound["settings"] == {"memory": "trimmed"}
    assert registry.active_alias(ComponentSlot.MEMORY) == "trimmed"


def test_settings_unknown_alias_is_visible(harness):
    registry = harness["registry"]
    doc = {"loop": {"alias": "direct"}, "settings": {"planner": "no-such-alias"}}
    with pytest.raises(AgentBindError, match="settings.planner"):
        bind_agent(registry, doc)


# ── model ────────────────────────────────────────────────────────────────────


def test_model_lands_on_context_and_rebuilds_llm_slots(harness):
    registry, context = harness["registry"], harness["context"]
    before = registry.active(ComponentSlot.EXECUTOR)
    fake = FakeModel("qwen:latest")
    bound = bind_agent(
        registry,
        {"loop": {"alias": "direct"}, "model": {"provider": "ollama", "id": "qwen:latest"}},
        model_factory=lambda spec: fake,
    )
    assert bound["model"] == {"provider": "ollama", "id": "qwen:latest"}
    assert context.llm is fake
    # the LLM-capturing executor was REBUILT against the new provider
    after = registry.active(ComponentSlot.EXECUTOR)
    assert after is not before
    assert after.llm is fake


def test_model_factory_failure_is_visible(harness):
    registry = harness["registry"]
    with pytest.raises(AgentBindError, match="bind model"):
        bind_agent(
            registry,
            {"loop": {"alias": "direct"}, "model": {"provider": "ollama", "id": "x"}},
            model_factory=lambda spec: (_ for _ in ()).throw(RuntimeError("no model backend")),
        )


def test_model_unknown_provider_is_visible(harness):
    registry = harness["registry"]
    doc = {"loop": {"alias": "direct"}, "model": {"provider": "quantum", "id": "x"}}
    with pytest.raises(AgentBindError, match="no factory for provider"):
        bind_agent(registry, doc)


# ── mcp (the pinned "bind merges tools" + visibility) ────────────────────────


def test_mcp_binds_pool_and_merges_tools_into_registry(harness):
    registry, context, tools = harness["registry"], harness["context"], harness["tools"]
    pool = harness["pool"]()
    doc = {
        "loop": {"alias": "direct"},
        "mcp": [{"alias": "web", "transport": "http", "url": "http://fake"}],
    }
    bound = bind_agent(registry, doc, pool=pool)
    assert bound["mcp"] == {"web": ["mcp_web_echo"]}
    assert "mcp_web_echo" in tools.names()
    # the executor's own path — ToolRegistry.execute → adapter → pool → fake:
    from tools.tool_call import ToolCall

    assert tools.execute(ToolCall(id="c1", name="mcp_web_echo", args={"x": 1})) == 'fake:echo:{"x": 1}'
    # pooled: one connection, no per-turn spawn
    assert pool.spawn_count() == 1
    pool.drain()


def test_mcp_unreachable_is_a_visible_bind_failure(harness):
    registry = harness["registry"]
    failing_pool = McpPool(factory=FakeUnreachable())
    doc = {
        "loop": {"alias": "direct"},
        "mcp": [{"alias": "web", "transport": "http", "url": "http://127.0.0.1:1"}],
    }
    with pytest.raises(AgentBindError, match="bind mcp\\[web\\]"):
        bind_agent(registry, doc, pool=failing_pool)
    # never a silent empty toolset:
    assert "mcp_web_echo" not in registry.context.tools.names()
    failing_pool.drain()


def test_rebind_drops_stale_alias_adapters(harness):
    registry, tools = harness["registry"], harness["tools"]
    pool = harness["pool"]()
    bind_agent(
        registry,
        {"loop": {"alias": "direct"}, "mcp": [{"alias": "web", "transport": "http", "url": "http://a"}]},
        pool=pool,
    )
    assert "mcp_web_echo" in tools.names()
    # a new agent without the alias: the stale adapter must go
    bind_agent(registry, {"loop": {"alias": "direct"}}, pool=pool)
    assert "mcp_web_echo" not in tools.names()
    pool.drain()


def test_rebind_same_alias_resets_pool(harness):
    registry = harness["registry"]
    pool = harness["pool"]()
    doc = {"loop": {"alias": "direct"}, "mcp": [{"alias": "web", "transport": "http", "url": "http://a"}]}
    bind_agent(registry, doc, pool=pool)
    first = pool.get("web")
    bind_agent(registry, doc, pool=pool)
    second = pool.get("web")
    assert first is not second and first.closed is True  # reset = drop + re-spawn
    assert pool.spawn_count() == 2
    pool.drain()


# ── skills + persona ─────────────────────────────────────────────────────────


def test_skill_fragment_and_prompt_compose_the_persona(harness):
    registry, context = harness["registry"], harness["context"]
    doc = {
        "loop": {"alias": "direct"},
        "prompt": "You are a research specialist. Cite sources.",
        "skill": [{"path": "citation.md"}],
    }
    bound = bind_agent(
        registry, doc,
        agent_id="researcher",
        persona_dir=os.path.join(harness["root"], "persona"),
        skill_root=harness["root"],
    )
    assert bound["persona"].endswith("researcher.persona.md")
    assert bound["skills"] == ["citation.md"]
    assert context.config.agent_path == bound["persona"]
    text = open(bound["persona"], encoding="utf-8").read()
    assert "research specialist" in text
    assert "Cite your sources" in text
    assert "Default persona" not in text  # the prompt REPLACED the AGENTS.md default


def test_skill_without_prompt_keeps_the_default_base(harness):
    registry, context = harness["registry"], harness["context"]
    doc = {"loop": {"alias": "direct"}, "skill": [{"path": "citation.md"}]}
    bound = bind_agent(
        registry, doc,
        persona_dir=os.path.join(harness["root"], "persona"),
        skill_root=harness["root"],
    )
    text = open(bound["persona"], encoding="utf-8").read()
    assert "Default persona" in text
    assert "Cite your sources" in text


def test_skill_declared_tool_must_resolve(harness):
    registry, tools = harness["registry"], harness["tools"]

    class Bundled:
        name = "bundled_tool"

    tools.register(Bundled())
    persona_dir = os.path.join(harness["root"], "persona")
    doc = {"loop": {"alias": "direct"}, "skill": [{"path": "citation.md", "tools": ["bundled_tool"]}]}
    bound = bind_agent(registry, doc, skill_root=harness["root"], persona_dir=persona_dir)
    assert bound["skills"] == ["citation.md"]

    doc = {"loop": {"alias": "direct"}, "skill": [{"path": "citation.md", "tools": ["ghost_tool"]}]}
    with pytest.raises(AgentBindError, match="ghost_tool"):
        bind_agent(registry, doc, skill_root=harness["root"], persona_dir=persona_dir)


def test_skill_unreadable_is_visible(harness):
    registry = harness["registry"]
    doc = {"loop": {"alias": "direct"}, "skill": [{"path": "nope.md"}]}
    with pytest.raises(AgentBindError, match="bind skill"):
        bind_agent(registry, doc)


def test_persona_without_config_is_visible(harness):
    registry, context = harness["registry"], harness["context"]
    context.config = None
    doc = {"loop": {"alias": "direct"}, "prompt": "x"}
    with pytest.raises(AgentBindError, match="AgentConfig"):
        bind_agent(registry, doc)
    context.config = AgentConfig()


# ── the lazy per-turn hook (signature no-op + restore) ──────────────────────


def test_ensure_agent_bound_is_idempotent_and_restores(harness, monkeypatch):
    import components.agent_bind as agent_bind
    import components.agent_store as agent_store_module

    registry, context, agents, graphs = (
        harness["registry"], harness["context"], harness["agents"], harness["graphs"],
    )
    # point the lazy hook at THIS harness's store (it reads the shared default)
    monkeypatch.setattr(agent_store_module, "get_agent_store", lambda: agents)
    agents.save("researcher", {
        "prompt": "You are a research specialist.",
        "loop": {"ref": "killer-research"},
        "skill": [{"path": "citation.md"}],
    })
    agents.set_active("researcher")

    # first turn: binds
    agent_bind.ensure_agent_bound(registry)
    first_path = context.config.agent_path
    assert first_path.endswith("researcher.persona.md")
    assert graphs.active_id == "killer-research"

    # next turn: the SAME document is a no-op (no persona rewrite, no re-activate)
    state_before = agent_bind._extras(context).get(agent_bind._EXTRAS_BIND_STATE)
    assert agent_bind.ensure_agent_bound(registry) is None
    assert agent_bind._extras(context).get(agent_bind._EXTRAS_BIND_STATE) == state_before

    # deactivation (delete): the default persona comes back
    agents.delete("researcher")
    assert agent_bind.ensure_agent_bound(registry) is None
    assert context.config.agent_path == str(harness["root"] + "/AGENTS.md")


def test_ensure_agent_bound_failure_is_visible(harness, monkeypatch):
    import components.agent_bind as agent_bind
    import components.agent_store as agent_store_module

    registry, agents = harness["registry"], harness["agents"]
    monkeypatch.setattr(agent_store_module, "get_agent_store", lambda: agents)
    agents.save(
        "dead",
        {
            "prompt": "x",
            "loop": {"alias": "direct"},
            "mcp": [{"alias": "web", "transport": "http", "url": "http://127.0.0.1:1"}],
        },
    )
    agents.set_active("dead")
    pool = McpPool(factory=FakeUnreachable())
    with pytest.raises(AgentBindError, match="bind mcp\\[web\\]"):
        agent_bind.ensure_agent_bound(registry, pool=pool)
    pool.drain()


# ── detach (the delete path: roll the shared state back) ─────────────────────


def test_detach_restores_the_full_pre_bind_state(harness):
    """The delete path's pin: deleting the bound agent returns EVERY shared
    seam the bind mutated — loop slot + active graph, model (+ rebuild), MCP
    adapters, settings aliases, persona — to its pre-bind value."""
    registry, context, tools, graphs = (
        harness["registry"], harness["context"], harness["tools"], harness["graphs"],
    )
    pool = harness["pool"]()
    default_llm = context.llm
    default_agent_path = context.config.agent_path
    doc = {
        "prompt": "You are a research specialist. Cite sources.",
        "loop": {"ref": "killer-research"},
        "model": {"provider": "ollama", "id": "qwen:latest"},
        "mcp": [{"alias": "web", "transport": "http", "url": "http://fake"}],
        "skill": [{"path": "citation.md"}],
        "settings": {"memory": "trimmed"},
    }
    bind_and_remember(
        registry,
        doc,
        agent_id="researcher",
        pool=pool,
        model_factory=lambda spec: FakeModel(spec.get("id")),
        skill_root=harness["root"],
        persona_dir=os.path.join(harness["root"], "persona"),
    )
    # the bind landed on every shared seam (the bug being fixed)
    assert registry.active_alias(ComponentSlot.LOOP) == "graph"
    assert graphs.active_id == "killer-research"
    assert context.llm is not default_llm
    assert "mcp_web_echo" in tools.names()
    assert registry.active_alias(ComponentSlot.MEMORY) == "trimmed"
    assert context.config.agent_path.endswith("researcher.persona.md")

    # delete it: every seam rolls back to the pre-bind pipeline
    assert detach_agent(registry, "researcher", pool=pool, graph_store=graphs) is True
    assert registry.active_alias(ComponentSlot.LOOP) == "plan_execute"  # pre-bind alias
    assert graphs.active_id is None  # pre-bind active graph
    assert context.llm is default_llm
    assert registry.active(ComponentSlot.EXECUTOR).llm is default_llm  # rebuilt
    assert "mcp_web_echo" not in tools.names()
    assert registry.active_alias(ComponentSlot.MEMORY) == "default"  # pre-bind alias
    assert context.config.agent_path == default_agent_path
    # idempotent: nothing left to roll back
    assert detach_agent(registry, "researcher", pool=pool, graph_store=graphs) is False
    pool.drain()


def test_deactivate_rolls_back_whatever_is_bound(harness):
    """The deactivate door at the bind seam (contrast the delete path): it
    rolls WHATEVER is bound back to the pre-bind snapshot — the store is not
    involved here, so the document question is out of scope — and a second
    call is a no-op while the same bind can be redone afterwards."""
    registry, context, tools, graphs = (
        harness["registry"], harness["context"], harness["tools"], harness["graphs"],
    )
    pool = harness["pool"]()
    default_llm = context.llm
    default_agent_path = context.config.agent_path
    doc = {
        "prompt": "You are a research specialist. Cite sources.",
        "loop": {"ref": "killer-research"},
        "model": {"provider": "ollama", "id": "qwen:latest"},
        "mcp": [{"alias": "web", "transport": "http", "url": "http://fake"}],
        "skill": [{"path": "citation.md"}],
        "settings": {"memory": "trimmed"},
    }
    bind_and_remember(
        registry,
        doc,
        agent_id="researcher",
        pool=pool,
        model_factory=lambda spec: FakeModel(spec.get("id")),
        skill_root=harness["root"],
        persona_dir=os.path.join(harness["root"], "persona"),
    )
    assert registry.active_alias(ComponentSlot.LOOP) == "graph"
    assert graphs.active_id == "killer-research"
    assert context.llm is not default_llm
    assert "mcp_web_echo" in tools.names()
    assert registry.active_alias(ComponentSlot.MEMORY) == "trimmed"

    # deactivate: same single-level undo as the delete path — no id needed
    assert deactivate_agent(registry, pool=pool, graph_store=graphs) is True
    assert registry.active_alias(ComponentSlot.LOOP) == "plan_execute"  # pre-bind alias
    assert graphs.active_id is None  # pre-bind active graph
    assert context.llm is default_llm
    assert registry.active(ComponentSlot.EXECUTOR).llm is default_llm  # rebuilt
    assert "mcp_web_echo" not in tools.names()
    assert registry.active_alias(ComponentSlot.MEMORY) == "default"  # pre-bind alias
    assert context.config.agent_path == default_agent_path
    # idempotent: nothing left to roll back
    assert deactivate_agent(registry, pool=pool, graph_store=graphs) is False
    # ...and the same bind can run again (deactivate is not a one-shot latch)
    bind_and_remember(
        registry,
        doc,
        agent_id="researcher",
        pool=pool,
        model_factory=lambda spec: FakeModel(spec.get("id")),
        skill_root=harness["root"],
        persona_dir=os.path.join(harness["root"], "persona"),
    )
    assert registry.active_alias(ComponentSlot.LOOP) == "graph"
    assert deactivate_agent(registry, pool=pool, graph_store=graphs) is True
    assert context.llm is default_llm
    pool.drain()


def test_deactivate_noop_without_bind_state(harness):
    """Nothing bound: deactivate is a False no-op (and the pre-bind seams
    stay exactly where they are)."""
    registry = harness["registry"]
    assert deactivate_agent(registry) is False
    assert registry.active_alias(ComponentSlot.LOOP) == "plan_execute"


def test_detach_only_rolls_back_the_bound_agent(harness):
    """Deleting an agent that is NOT the bound one must change no runtime
    state — and the bound agent's own deletion still restores."""
    registry, context = harness["registry"], harness["context"]
    pool = harness["pool"]()
    default_llm = context.llm
    bind_and_remember(
        registry,
        {"loop": {"alias": "direct"}, "model": {"provider": "ollama", "id": "qwen:latest"}},
        agent_id="keeper",
        pool=pool,
        model_factory=lambda spec: FakeModel(spec.get("id")),
    )
    assert context.llm is not default_llm
    assert registry.active_alias(ComponentSlot.LOOP) == "direct"

    assert detach_agent(registry, "someone-else", pool=pool) is False
    assert context.llm is not default_llm  # untouched
    assert registry.active_alias(ComponentSlot.LOOP) == "direct"

    assert detach_agent(registry, "keeper", pool=pool) is True
    assert context.llm is default_llm
    assert registry.active_alias(ComponentSlot.LOOP) == "plan_execute"
    pool.drain()
