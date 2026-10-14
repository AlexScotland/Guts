"""Per-agent OWNED loop (docs/agent-graph.md — the one loop child is the agent's
private turn-graph: it OWNS it 1:1, created with it, cascade-deleted with it).

Pinned behaviors (fakes only — the repo's frozen rule), red -> green:
  * a graph may carry ``owner: <agent-id>`` — a store-level lifecycle field
    (the graph validator ignores unknown top-level keys, so ownership never
    touches the pure v0 contract); ``owner_of`` / ``owned_by`` /
    ``delete_owned_by`` + ``owner`` in ``list_meta``;
  * ``owner`` shape-checked at save: a non-token owner is a GraphError (a 400);
  * POST /api/agents/{id}/loop — the agent's loop is BUILT here: a loop doc
    with ``owner == agent_id`` is saved and the agent's ``loop.ref`` points
    at it together (a ref can never dangle); idempotent (reuses the same id);
    an invalid supplied graph is a 400, never saved;
  * DELETE /api/agents/{id} — the agent's OWNED loop cascades away, but a
    SHARED loop (an agent that merely references it) survives;
  * activation still binds-then-runs the EXISTING loop (the ref now resolves
    to the agent's owned loop — no new execution path).

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/test_agent_loop_ownership.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import server


# ── a graph the agent OWNS vs a graph it only references (shared) ────────────


def _loop(**node_over):
    """A minimal VALID loop: one seeded node (valid against the frozen v0
    contract — one entry, no edges). ``node_over`` can replace the seed node."""
    doc = {
        "name": "loop",
        "nodes": {"seed": dict(node_over or {"primitive": "prompt", "config": {"text": "frame"}})},
        "edges": [],
    }
    return doc


#: A loop a second, INDEPENDENT agent references (owner = the OTHER agent),
#: so a cascade on agent A must NOT touch it.
_OWNED_LOOP = _loop()


class _FakeStructured:
    def invoke(self, messages):
        from goals.goal import Goal

        return Goal(intent="fake")


class _FakeLLM:
    def with_structured_output(self, schema):
        return _FakeStructured()


class _FakeModel:
    def __init__(self, model_id):
        self.model_id = model_id

    def with_tools(self, tools):
        return self

    def with_structured_output(self, schema):
        return _FakeStructured()


@pytest.fixture()
def loop_client(tmp_path, monkeypatch):
    """Temp graph + agent stores wired into the server, plus a bind-path fake
    model (no Ollama) so activation of an owned loop is testable end-to-end.
    Returns (client, graph_store, agent_store)."""
    from components import (
        ComponentContext,
        ComponentRegistry,
        register_agent_components,
        register_builtin_components,
        register_graph_components,
    )
    import components.agent_bind as agent_bind
    from components.agent_store import AgentStore
    from components.graph_store import GraphStore
    from tools.tool_registry import ToolRegistry

    graphs = GraphStore(os.path.join(str(tmp_path), "graphs"))
    agents = AgentStore(
        os.path.join(str(tmp_path), "agents"),
        graph_ids=lambda: graphs.ids(),
        skill_root=str(tmp_path / "skills"),
    )
    (tmp_path / "skills").mkdir(exist_ok=True)

    monkeypatch.setattr(agent_bind, "_default_model_factory", lambda spec: _FakeModel(spec.get("id")))

    from agent_config import AgentConfig

    context = ComponentContext(llm=_FakeLLM(), tools=ToolRegistry(), config=AgentConfig(agent_path=str(tmp_path / "AGENTS.md")))
    registry = ComponentRegistry(context)
    register_builtin_components(registry, context)
    register_graph_components(registry, context, graph_store=graphs)
    register_agent_components(registry, agent_store=agents)

    monkeypatch.setattr(server, "get_registry", lambda: registry)
    monkeypatch.setattr(server, "get_graph_store", lambda: graphs)
    monkeypatch.setattr(server, "get_agent_store", lambda: agents)
    server.app.state.registry = registry
    server.app.state.context = context
    server.app.state.graphs = graphs
    return TestClient(server.app), graphs, agents


def _valid_agent(**overrides):
    doc = {"prompt": "A specialist.", "loop": {"alias": "direct"}}
    doc.update(overrides)
    return doc


# ── store: ownership (a store-level lifecycle field) ─────────────────────────


def test_owner_roundtrips_and_is_surfaced(loop_client):
    _, graphs, _ = loop_client
    graphs.save("r__loop", _loop(), name="r loop")
    graphs.save("r__loop", {**_loop(), "owner": "r"}, name="r loop")  # re-save with owner
    assert graphs.owner_of("r__loop") == "r"
    assert graphs.owned_by("r") == ["r__loop"]
    meta = next(m for m in graphs.list_meta() if m["id"] == "r__loop")
    assert meta["owner"] == "r"
    # the doc itself carries ownership (files are the store)
    assert graphs.get("r__loop")["owner"] == "r"


def test_unowned_graph_is_shared_and_inert_to_cascade(loop_client):
    _, graphs, _ = loop_client
    graphs.save("shared", _loop(), name="shared")
    assert graphs.owner_of("shared") is None
    assert graphs.owned_by("nobody") == []
    assert graphs.delete_owned_by("nobody") == []
    assert graphs.has("shared")  # a cascade never touches a shared loop


def test_save_rejects_malformed_owner(loop_client):
    from components.graph_store import GraphError

    _, graphs, _ = loop_client
    with pytest.raises(GraphError):
        graphs.save("bad", {**_loop(), "owner": "not a token!"})
    assert not graphs.has("bad")
    # present-but-none and absent are both legal (shared loop)
    graphs.save("ok-none", {**_loop(), "owner": None}, name="shared")
    assert graphs.owner_of("ok-none") is None


# ── the door: POST /api/agents/{id}/loop builds the agent's private loop ─────


def test_create_agent_loop_builds_owned_loop_and_attaches_ref(loop_client):
    client, graphs, _ = loop_client
    client.post("/api/agents", json={"id": "sage", "name": "Sage", "agent": _valid_agent()})
    res = client.post("/api/agents/sage/loop", json={})
    assert res.status_code == 200
    body = res.json()
    assert body["agent_id"] == "sage"
    assert body["owner"] == "sage"
    assert body["node_count"] >= 1

    ref = body["ref"]
    # the loop is a first-class graph the agent OWNS (owner stamped by the door)
    loop_doc = graphs.get(ref)
    assert loop_doc["owner"] == "sage"
    assert graphs.owner_of(ref) == "sage"
    # and the agent's loop.ref now points at it (created together → no dangling ref)
    agent = client.get("/api/agents/sage").json()
    assert agent["agent"]["loop"]["ref"] == ref
    # discoverable on the graphs door, marked as owned
    listed = next(g for g in client.get("/api/graphs").json()["graphs"] if g["id"] == ref)
    assert listed["owner"] == "sage"


def test_create_agent_loop_is_idempotent_and_stays_one_to_one(loop_client):
    client, graphs, _ = loop_client
    client.post("/api/agents", json={"id": "sage", "agent": _valid_agent()})
    first = client.post("/api/agents/sage/loop", json={}).json()["ref"]
    second = client.post("/api/agents/sage/loop", json={}).json()["ref"]
    assert first == second  # same agent → same private loop (not a second one)
    assert graphs.owned_by("sage") == [first]  # exactly ONE owned loop


def test_create_agent_loop_replaces_the_seed(loop_client):
    client, graphs, _ = loop_client
    client.post("/api/agents", json={"id": "sage", "agent": _valid_agent()})
    custom = {
        "name": "custom loop",
        "nodes": {
            "frame": {"primitive": "prompt", "config": {"text": "be precise"}},
            "answer": {"primitive": "act", "config": {"task": "answer"}},
        },
        "edges": [{"from": "frame", "to": "answer", "type": "data"}],
    }
    res = client.post("/api/agents/sage/loop", json={"name": "custom", "graph": custom})
    assert res.status_code == 200
    saved = graphs.get(res.json()["ref"])
    assert saved["owner"] == "sage"  # the door re-stamps ownership, even on replace
    assert set(saved["nodes"]) == {"frame", "answer"}
    assert saved["name"] == "custom"


def test_create_agent_loop_invalid_graph_is_400_and_not_saved(loop_client):
    client, graphs, agents = loop_client
    client.post("/api/agents", json={"id": "sage", "agent": _valid_agent()})
    res = client.post("/api/agents/sage/loop", json={"graph": {"nodes": {}, "edges": []}})
    assert res.status_code == 400
    assert "nodes" in res.json()["detail"]
    # not created, not attached — the agent is untouched
    assert graphs.owned_by("sage") == []
    assert agents.get("sage")["loop"] == {"alias": "direct"}


def test_create_agent_loop_rejects_wrong_owner(loop_client):
    client, _, _ = loop_client
    client.post("/api/agents", json={"id": "sage", "agent": _valid_agent()})
    res = client.post("/api/agents/sage/loop", json={"graph": {**_loop(), "owner": "intruder"}})
    assert res.status_code == 400
    assert "owner" in res.json()["detail"]


def test_create_agent_loop_unknown_agent_is_404(loop_client):
    client, _, _ = loop_client
    res = client.post("/api/agents/nobody/loop", json={})
    assert res.status_code == 404
    assert "unknown agent" in res.json()["detail"]


# ── cascade: an agent's private loop dies with the agent ─────────────────────


def test_delete_agent_cascades_its_owned_loop(loop_client):
    client, graphs, _ = loop_client
    client.post("/api/agents", json={"id": "sage", "agent": _valid_agent()})
    ref = client.post("/api/agents/sage/loop", json={}).json()["ref"]
    assert graphs.has(ref)

    res = client.delete("/api/agents/sage")
    assert res.status_code == 200
    assert not graphs.has(ref)  # the private loop cascaded away
    assert client.get("/api/graphs").json()["graphs"] == []


def test_delete_agent_keeps_a_shared_referenced_loop(loop_client):
    client, graphs, _ = loop_client
    # A SHARED loop (no owner) two agents both reference:
    graphs.save("shared", _loop(), name="shared")
    shared = _valid_agent(loop={"ref": "shared"})
    client.post("/api/agents", json={"id": "a", "agent": shared})
    client.post("/api/agents", json={"id": "b", "agent": shared})

    assert client.delete("/api/agents/a").status_code == 200
    # the shared loop that 'b' still references must SURVIVE the delete of 'a'
    assert graphs.has("shared")
    assert graphs.owner_of("shared") is None
    # and deleting 'b' (its owner would be 'b', not a shared owner) still leaves it
    client.delete("/api/agents/b")
    assert graphs.has("shared")


def test_build_loop_is_the_loop_that_runs(loop_client):
    """Activation binds-then-runs the EXISTING loop (the ref now resolves to
    the agent's OWNED loop) — the crux that the owned loop needs no new path."""
    client, graphs, _ = loop_client
    client.post("/api/agents", json={"id": "sage", "name": "Sage", "agent": _valid_agent(
        model={"provider": "ollama", "id": "qwen:latest"},
    )})
    ref = client.post("/api/agents/sage/loop", json={}).json()["ref"]
    res = client.post("/api/agents/sage/activate")
    assert res.status_code == 200

    from components import ComponentSlot

    registry = client.app.state.registry
    assert registry.active_alias(ComponentSlot.LOOP) == "graph"   # the graph loop is active
    assert graphs.active_id == ref                                  # ...and it is the agent's OWNED loop
