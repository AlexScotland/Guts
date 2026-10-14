"""HTTP contract tests for the /api/agents door (v1, docs/agent-graph.md).

Pinned behaviors (the doc's "Tests — activation group", red -> green):
  * GET/POST/DELETE /api/agents + POST /api/agents/{id}/activate — agents
    as JSON files, validated at save time (an invalid agent is a 400, never
    a saved file);
  * saving is NOT activation — the doors stay separate (like /api/graphs);
  * activate flips the active id; unknown id is a 404, not a 500;
  * describe() gains the ``agents`` block, parallel to its ``graphs``
    block — the single discovery door (GET /api/components);
  * the one shape family: GET/POST/DELETE/activate all return
    ``{active, agents: [...]}``.

No network, no real LLM: the stores are temp-dir fakes, exactly like the
graph API suite (the repo's frozen fakes-only rule).

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/test_agent_api.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import server


def _valid_agent(**overrides):
    doc = {
        "prompt": "You are a research specialist. Cite sources.",
        "model": {"provider": "ollama", "id": "qwen:latest"},
        "mcp": [
            {"alias": "web", "transport": "sse", "url": "http://searxng:8080/mcp"},
        ],
        "skill": [{"path": "citation.md"}],
        "settings": {"memory": "trimmed"},
        "loop": {"ref": "killer-research"},
    }
    doc.update(overrides)
    return doc


class _FakeStructured:
    def invoke(self, messages):
        from goals.goal import Goal

        return Goal(intent="fake intent")


class _FakeLLM:
    def with_structured_output(self, schema):
        return _FakeStructured()


@pytest.fixture()
def agent_client(tmp_path, monkeypatch):
    from components import (
        ComponentContext,
        ComponentRegistry,
        register_agent_components,
        register_builtin_components,
        register_graph_components,
    )
    from components.agent_store import AgentStore
    from components.graph_store import GraphStore
    from tools.tool_registry import ToolRegistry

    graphs = GraphStore(os.path.join(str(tmp_path), "api-graphs"))
    agents = AgentStore(
        os.path.join(str(tmp_path), "api-agents"),
        graph_ids=lambda: {"killer-research"},  # the save-time seam fixture
        skill_root=str(tmp_path / "skills"),
    )
    (tmp_path / "skills").mkdir(exist_ok=True)
    (tmp_path / "skills" / "citation.md").write_text("# Cite your sources\n", encoding="utf-8")

    context = ComponentContext(llm=_FakeLLM(), tools=ToolRegistry(), config=None)
    registry = ComponentRegistry(context)
    register_builtin_components(registry, context)
    register_graph_components(registry, context, graph_store=graphs)
    register_agent_components(registry, agent_store=agents)
    monkeypatch.setattr(server, "get_registry", lambda: registry)
    monkeypatch.setattr(server, "get_graph_store", lambda: graphs)
    monkeypatch.setattr(server, "get_agent_store", lambda: agents)
    return TestClient(server.app), agents


# ── /api/agents CRUD ────────────────────────────────────────────────────────


def test_agents_list_starts_empty(agent_client):
    client, _ = agent_client
    res = client.get("/api/agents")
    assert res.status_code == 200
    assert res.json() == {"active": None, "agents": []}


def test_save_lists_and_persists_by_id(agent_client):
    client, store = agent_client
    res = client.post(
        "/api/agents",
        json={"id": "researcher", "name": "Deep Researcher", "agent": _valid_agent()},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["active"] is None  # saving is not activation — the doors stay separate
    entry = next(a for a in body["agents"] if a["id"] == "researcher")
    assert entry["name"] == "Deep Researcher"
    assert entry["active"] is False
    assert entry["loop"] == "killer-research"
    assert entry["model"] == "qwen:latest"
    assert entry["mcp"] == ["web"]
    assert entry["skills"] == ["citation.md"]
    assert entry["has_prompt"] is True
    assert entry["settings"] == {"memory": "trimmed"}
    assert store.has("researcher")
    # persisted as a file (files are the store)
    assert os.path.isfile(os.path.join(store.directory, "researcher.json"))
    listed = client.get("/api/agents").json()["agents"]
    assert [a["id"] for a in listed] == ["researcher"]


def test_save_invalid_agent_is_rejected_not_persisted(agent_client):
    client, store = agent_client
    doc = _valid_agent()
    del doc["loop"]
    res = client.post("/api/agents", json={"id": "broken", "agent": doc})
    assert res.status_code == 400
    assert "loop" in res.json()["detail"]
    assert not store.has("broken")
    assert client.get("/api/agents/broken").status_code == 404


def test_save_rejects_unknown_loop_ref(agent_client):
    client, _ = agent_client
    res = client.post(
        "/api/agents",
        json={"id": "ghostref", "agent": _valid_agent(loop={"ref": "no-such-graph"})},
    )
    assert res.status_code == 400
    assert "not a saved graph" in res.json()["detail"]


def test_save_rejects_unknown_loop_alias(agent_client):
    client, _ = agent_client
    res = client.post(
        "/api/agents",
        json={"id": "badalias", "agent": _valid_agent(loop={"alias": "quantum"})},
    )
    assert res.status_code == 400


def test_save_rejects_doc_id_mismatch(agent_client):
    client, _ = agent_client
    res = client.post(
        "/api/agents",
        json={"id": "researcher", "agent": _valid_agent(id="another")},
    )
    assert res.status_code == 400


def test_save_rejects_missing_skill_file(agent_client):
    client, _ = agent_client
    res = client.post(
        "/api/agents",
        json={"id": "noskill", "agent": _valid_agent(skill=[{"path": "nope.md"}])},
    )
    assert res.status_code == 400


def test_get_document_returns_id_name_and_agent(agent_client):
    client, _ = agent_client
    client.post("/api/agents", json={"id": "r", "name": "R", "agent": _valid_agent()})
    res = client.get("/api/agents/r")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "r"
    assert body["name"] == "R"
    assert body["agent"]["loop"]["ref"] == "killer-research"


def test_get_document_unknown_id_is_404(agent_client):
    client, _ = agent_client
    assert client.get("/api/agents/nope").status_code == 404


def test_delete_removes_and_family_shape(agent_client):
    client, store = agent_client
    client.post("/api/agents", json={"id": "r", "agent": _valid_agent()})
    res = client.delete("/api/agents/r")
    assert res.status_code == 200
    assert res.json() == {"active": None, "agents": []}
    assert client.delete("/api/agents/r").status_code == 404
    assert not os.path.exists(os.path.join(store.directory, "r.json"))


# ── activation + the discovery door ─────────────────────────────────────────


def test_activate_flips_active_id(agent_client):
    client, store = agent_client
    client.post("/api/agents", json={"id": "a", "agent": _valid_agent()})
    client.post("/api/agents", json={"id": "b", "agent": _valid_agent()})
    res = client.post("/api/agents/a/activate")
    assert res.status_code == 200
    body = res.json()
    assert body["active"] == "a"
    assert [e["active"] for e in body["agents"] if e["id"] == "a"] == [True]
    assert store.active_id == "a"
    # the pointer persists (files are the store)
    assert os.path.isfile(os.path.join(store.directory, "_meta.json"))
    # re-activate the other: the active flag follows
    res = client.post("/api/agents/b/activate")
    assert res.json()["active"] == "b"
    assert [e["active"] for e in res.json()["agents"] if e["id"] == "a"] == [False]


def test_activate_unknown_agent_is_404(agent_client):
    client, _ = agent_client
    res = client.post("/api/agents/ghost/activate")
    assert res.status_code == 404


def test_delete_active_agent_clears_active(agent_client):
    client, store = agent_client
    client.post("/api/agents", json={"id": "a", "agent": _valid_agent()})
    client.post("/api/agents/a/activate")
    assert client.delete("/api/agents/a").json()["active"] is None
    assert store.active_id is None


def test_describe_carries_agents_block_parallel_to_graphs(agent_client):
    client, store = agent_client
    client.post("/api/agents", json={"id": "a", "name": "A", "agent": _valid_agent()})
    describe = client.get("/api/components").json()
    assert "graphs" in describe
    assert describe["agents"] == describe_agents_shape(store)
    assert describe["agents"]["active"] is None
    assert describe["agents"]["agents"][0]["id"] == "a"


def describe_agents_shape(store):
    from components.agent_store import describe_agents

    return describe_agents(store)
