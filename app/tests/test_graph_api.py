"""HTTP contract tests for the graph endpoint (v0, docs/graph-loop-designer.md).

Pinned behaviors:
  * GET/POST /api/graphs + DELETE /api/graphs/{id} — graphs as JSON files,
    validated at save time (an invalid graph is a 400, never a saved file);
  * POST /api/components/activate — the loop/graph_id door:
    {"loop": "graph", "graph_id": "..."} makes a saved graph the active
    workflow, exclusive with preset/slot activation;
  * GET /api/components — the single discovery door: the 8-primitive
    vocabulary AND the saved/active graphs, in one response.

No network, no real LLM: the registry/store are in-process fakes, exactly
like the rest of the contract suite.

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import server

# ── fakes ────────────────────────────────────────────────────────────────────


class FakeStructured:
    def invoke(self, messages):
        from goals.goal import Goal

        return Goal(intent="fake intent")


class FakeLLM:
    def with_structured_output(self, schema):
        return FakeStructured()

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return AIMessage(content="fake-llm-answer")


SERIAL_DOC = {
    "name": "serial",
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


@pytest.fixture()
def graph_client(monkeypatch, tmp_path):
    from components import (
        ComponentContext,
        ComponentRegistry,
        register_builtin_components,
        register_graph_components,
    )
    from components.graph_store import GraphStore
    from tools.tool_registry import ToolRegistry

    store = GraphStore(os.path.join(str(tmp_path), "api-graphs"))
    context = ComponentContext(llm=FakeLLM(), tools=ToolRegistry(), config=None)
    registry = ComponentRegistry(context)
    register_builtin_components(registry, context)
    register_graph_components(registry, context, graph_store=store)
    monkeypatch.setattr(server, "get_registry", lambda: registry)
    monkeypatch.setattr(server, "get_graph_store", lambda: store)
    return TestClient(server.app), store, registry


# ── /api/graphs CRUD ─────────────────────────────────────────────────────────


def test_graphs_list_starts_empty(graph_client):
    client, store, _ = graph_client
    res = client.get("/api/graphs")
    assert res.status_code == 200
    assert res.json() == {"active": None, "graphs": []}


def test_save_lists_and_persists_by_id(graph_client):
    client, store, _ = graph_client
    res = client.post("/api/graphs", json={"id": "g1", "name": "serial", "graph": SERIAL_DOC})
    assert res.status_code == 200
    body = res.json()
    # POST returns the same {active, graphs: [...]} family shape as GET/DELETE
    # (a bare entry here once broke the designer's saveGraph parse callback).
    assert body["active"] is None  # saving is not activation — the doors stay separate
    saved = next(g for g in body["graphs"] if g["id"] == "g1")
    assert saved["active"] is False
    assert saved["name"] == "serial"
    assert store.has("g1")
    listed = client.get("/api/graphs").json()["graphs"]
    assert [g["id"] for g in listed] == ["g1"]


def test_save_invalid_graph_is_rejected_not_persisted(graph_client):
    client, store, _ = graph_client
    bad = {"nodes": {"x": {"primitive": "vibecoding"}}, "edges": []}
    res = client.post("/api/graphs", json={"id": "bad", "graph": bad})
    assert res.status_code == 400
    assert "unknown primitive" in res.json()["detail"]
    assert not store.has("bad")
    assert client.get("/api/graphs").json()["graphs"] == []


def test_save_rejects_branch_side_effects(graph_client):
    client, store, _ = graph_client
    doc = {
        "nodes": {
            "p": {
                "primitive": "parallel",
                "branches": [
                    {
                        "name": "b1",
                        "graph": {
                            "nodes": {"r": {"primitive": "research"}, "a": {"primitive": "act"}},
                            "edges": [],
                        },
                    }
                ],
            }
        },
        "edges": [],
    }
    res = client.post("/api/graphs", json={"id": "forbidden", "graph": doc})
    assert res.status_code == 400
    assert "side-effect" in res.json()["detail"]
    assert not store.has("forbidden")


def test_save_rejects_unknown_id_shape(graph_client):
    client, store, _ = graph_client
    res = client.post("/api/graphs", json={"id": "../escape", "graph": SERIAL_DOC})
    assert res.status_code == 400


def test_get_document_returns_id_name_and_graph(graph_client):
    client, store, _ = graph_client
    client.post(
        "/api/graphs",
        json={"id": "g1", "name": "serial", "graph": SERIAL_DOC},
    )
    res = client.get("/api/graphs/g1")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == "g1"
    assert body["name"] == "serial"
    assert body["graph"]["nodes"]["do"]["primitive"] == "act"
    assert [e["to"] for e in body["graph"]["edges"]] == ["do", "judge"]


def test_get_document_unknown_id_is_404(graph_client):
    client, _, _ = graph_client
    res = client.get("/api/graphs/ghost")
    assert res.status_code == 404
    assert "unknown graph" in res.json()["detail"]


def test_delete_removes_and_clears_active(graph_client):
    client, store, _ = graph_client
    client.post("/api/graphs", json={"id": "g1", "graph": SERIAL_DOC})
    res = client.delete("/api/graphs/g1")
    assert res.status_code == 200
    assert res.json() == {"active": None, "graphs": []}
    assert client.delete("/api/graphs/g1").status_code == 404


# ── last-run: the API + canvas seam ─────────────────────────────────────────


def test_last_run_unknown_graph_is_404(graph_client):
    client, store, _ = graph_client
    res = client.get("/api/graphs/ghost/last-run")
    assert res.status_code == 404


def test_last_run_saved_but_never_run_is_404(graph_client):
    client, store, _ = graph_client
    client.post("/api/graphs", json={"id": "g1", "graph": SERIAL_DOC})
    res = client.get("/api/graphs/g1/last-run")
    assert res.status_code == 404
    assert "no run recorded" in res.json()["detail"]


def test_last_run_returns_the_recorded_run(graph_client):
    client, store, _ = graph_client
    client.post("/api/graphs", json={"id": "g1", "graph": SERIAL_DOC})
    store.record_run(
        "g1",
        {
            "status": "ok",
            "answer": "fake-llm-answer",
            "events": [
                {"event": "start", "node": "prep"},
                {"event": "done", "node": "judge"},
            ],
        },
    )
    res = client.get("/api/graphs/g1/last-run")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["answer"] == "fake-llm-answer"
    assert [e["node"] for e in body["events"]] == ["prep", "judge"]
    assert isinstance(body["at"], (int, float))


def test_delete_clears_last_run_record(graph_client):
    client, store, _ = graph_client
    client.post("/api/graphs", json={"id": "g1", "graph": SERIAL_DOC})
    store.record_run("g1", {"status": "ok", "answer": "x", "events": []})
    assert client.delete("/api/graphs/g1").status_code == 200
    assert client.get("/api/graphs/g1/last-run").status_code == 404


def test_last_run_returns_live_record_while_running(graph_client):
    """IN-FLIGHT run: the seam returns status=running + events so far —
    the shape the designer polls to highlight the current node. The live
    record takes precedence over any earlier finished record."""
    client, store, _ = graph_client
    client.post("/api/graphs", json={"id": "g1", "graph": SERIAL_DOC})
    store.record_run("g1", {"status": "ok", "answer": "old", "events": []})
    store.begin_run("g1")
    store.append_event("g1", {"event": "start", "node": "prep", "at": 1})
    store.append_event("g1", {"event": "done", "node": "prep", "at": 2})
    store.append_event("g1", {"event": "start", "node": "do", "at": 3})

    res = client.get("/api/graphs/g1/last-run")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "running"
    assert body["answer"] is None and body.get("error") is None
    assert [e["node"] for e in body["events"]] == ["prep", "prep", "do"]
    assert isinstance(body["at"], (int, float))


def test_last_run_falls_back_to_finished_record_once_run_ends(graph_client):
    """After the live record is cleared (run ended), the endpoint serves
    the finished record again — the same URL is the whole lifecycle."""
    client, store, _ = graph_client
    client.post("/api/graphs", json={"id": "g1", "graph": SERIAL_DOC})
    store.begin_run("g1")
    store.append_event("g1", {"event": "start", "node": "prep", "at": 1})
    res = client.get("/api/graphs/g1/last-run")
    assert res.json()["status"] == "running"

    store.record_run("g1", {"status": "failed", "error": "boom", "events": [
        {"event": "start", "node": "prep"}, {"event": "failure", "node": "prep"},
    ]})
    store.clear_live("g1")
    res = client.get("/api/graphs/g1/last-run")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "failed"
    assert body["error"] == "boom"


def test_delete_cleared_live_record_to(graph_client):
    """Deleting a graph drops its in-flight live record as well (parity
    with the finished-record cleanup)."""
    client, store, _ = graph_client
    client.post("/api/graphs", json={"id": "g1", "graph": SERIAL_DOC})
    store.begin_run("g1")
    store.append_event("g1", {"event": "start", "node": "prep", "at": 1})
    assert client.delete("/api/graphs/g1").status_code == 200
    assert store.live_run("g1") is None


# ── activation: the loop/graph_id door ──────────────────────────────────────


def test_activate_graph_loop_with_graph_id(graph_client):
    client, store, registry = graph_client
    client.post("/api/graphs", json={"id": "g1", "graph": SERIAL_DOC})
    res = client.post("/api/components/activate", json={"loop": "graph", "graph_id": "g1"})
    assert res.status_code == 200
    body = res.json()
    assert body["slots"]["loop"]["active"] == "graph"
    assert body["graphs"]["active"] == "g1"
    assert store.active_id == "g1"


def test_activate_graph_loop_without_id_switches_loop_only(graph_client):
    client, store, _ = graph_client
    res = client.post("/api/components/activate", json={"loop": "graph"})
    assert res.status_code == 200
    assert res.json()["slots"]["loop"]["active"] == "graph"
    assert store.active_id is None


def test_activate_graph_id_requires_graph_loop(graph_client):
    client, _, _ = graph_client
    res = client.post(
        "/api/components/activate", json={"loop": "direct", "graph_id": "g1"}
    )
    assert res.status_code == 400


def test_activate_scopes_are_exclusive(graph_client):
    client, _, _ = graph_client
    assert client.post(
        "/api/components/activate", json={"preset": "fast", "loop": "graph"}
    ).status_code == 400
    assert client.post("/api/components/activate", json={"loop": "graph"}).status_code == 200
    assert client.post("/api/components/activate", json={}).status_code == 400


def test_activate_unknown_loop_alias_is_400(graph_client):
    client, _, _ = graph_client
    res = client.post("/api/components/activate", json={"loop": "nope"})
    assert res.status_code == 400


def test_activate_unknown_graph_id_is_400(graph_client):
    client, _, _ = graph_client
    res = client.post(
        "/api/components/activate", json={"loop": "graph", "graph_id": "ghost"}
    )
    assert res.status_code == 400


# ── the one door: GET /api/components ───────────────────────────────────────


def test_components_describe_exposes_primitives_and_graphs(graph_client):
    client, store, _ = graph_client
    client.post("/api/graphs", json={"id": "g1", "name": "serial", "graph": SERIAL_DOC})
    body = client.get("/api/components").json()
    names = {p["name"] for p in body["primitives"]}
    # the 8 v0 primitives + route (v1, docs/ai-routing-nodes.md)
    assert names == {"prompt", "research", "plan", "act", "critic", "gate", "merge", "parallel", "route"}
    act = next(p for p in body["primitives"] if p["name"] == "act")
    assert act["side_effects"] is True
    route = next(p for p in body["primitives"] if p["name"] == "route")
    assert route["provides"] == ["context", "result"]
    assert "side_effects" not in route
    assert body["graphs"]["active"] is None  # saved but not activated
