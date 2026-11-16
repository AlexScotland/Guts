"""Face 1 — the design face as an MCP server (design/mcp_server.py).

Pinned behaviors (docs/graph-loop-designer.md, "AI-assisted design", Face 1):
  * the server is a HAND-ROLLED mirror of app/mcp/client.py's wire
    contract — same handshake (initialize + notifications/initialized),
    same tools/list descriptor shape, same tools/call result shape
    ({content: [{type: text, text}]}), isError = a VISIBLE tool failure
    (not a JSON-RPC error), every answer echoes the request id, and
    notifications never get a response;
  * the tools are EXACTLY Face 0's public surface (draft / validate /
    catalog / ports_of / wire_check) — same functions as the HTTP face,
    single source of truth in design.draft / design.catalog;
  * the integration path is this REPO's own client (StdioConnection)
    driving the spawned server — the mirror-image promise is the test,
    driven with a canned reply via the public set_completer seam.

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/ -v
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from design import draft as draft_mod
from design import mcp_server
from mcp.client import McpError, StdioConnection

USER_GOAL = (
    "I want a graph does research, then does a fact check, drops irrelevant "
    "knowledge and loops until a decision of if the AI feels like the "
    "original ask is completed"
)

# Kept in sync with tests/test_design_draft.py (the Face 0 canonical doc —
# the same acceptance case the model maps USER_GOAL to; here it doubles as
# the canned reply so the stdio test needs no model).
CANONICAL_DOC = {
    "name": "research-until-completed",
    "nodes": {
        "dig": {
            "primitive": "research",
            "config": {"task": "Gather authoritative, current facts that answer the goal."},
            "budget": 40,
        },
        "sift": {
            "primitive": "research",
            "config": {"task": "Drop knowledge that is irrelevant to the goal."},
            "budget": 20,
        },
        "judge": {
            "primitive": "critic",
            "config": {"criterion": "pass only when the remaining knowledge answers the original ask"},
        },
    },
    "edges": [
        {"from": "dig", "to": "sift", "type": "data"},
        {"from": "sift", "to": "judge", "type": "data"},
        {"from": "judge", "to": "dig", "type": "control", "max_passes": 8},
    ],
}

BAD_DOC = {  # valid-looking, but the edge leaves the node set entirely
    "name": "broken",
    "nodes": {"dig": {"primitive": "research", "config": {"task": "x"}, "budget": 5}},
    "edges": [{"from": "dig", "to": "nowhere", "type": "data"}],
}

TOOL_NAMES = ["draft", "validate", "catalog", "ports_of", "wire_check"]


class FakeCompleter:
    """Returns the canned document as JSON text for every call."""

    def __init__(self, doc):
        self.doc = doc

    def invoke(self, messages):
        return json.dumps(self.doc, indent=2)


def _request(method, params=None, msg_id=1):
    msg = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        msg["id"] = msg_id
    if params is not None:
        msg["params"] = params
    return msg


def _call(tool, args=None, msg_id=100):
    resp = mcp_server.handle_message(_request("tools/call", {"name": tool, "arguments": args or {}}, msg_id))
    assert "error" not in resp, resp  # a tool answer is a RESULT, even a failed one
    return resp["result"]


def _text(result):
    assert "isError" not in result, result
    return json.loads(result["content"][0]["text"])


def fake(monkeypatch):
    monkeypatch.setattr(draft_mod, "get_completer", lambda: FakeCompleter(CANONICAL_DOC))


# ── handshake + dispatch (the client's contract, mirrored) ──────────────────


def test_initialize_result_shape_and_notification_ack():
    resp = mcp_server.handle_message(_request("initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "repo-client"},
    }))
    assert resp["id"] == 1  # the client matches by id — it must be echoed
    result = resp["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert "serverInfo" in result and "capabilities" in result
    # notifications/initialized has no id: the client expects no answer
    assert mcp_server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_list_reports_the_face0_surface():
    result = mcp_server.handle_message(_request("tools/list", None, 2))["result"]
    assert [t["name"] for t in result["tools"]] == TOOL_NAMES
    for tool in result["tools"]:
        assert tool["description"]
        assert isinstance(tool["inputSchema"], dict) and tool["inputSchema"]["type"] == "object"
    draft = next(t for t in result["tools"] if t["name"] == "draft")
    assert draft["inputSchema"]["required"] == ["goal"]
    assert set(draft["inputSchema"]["properties"]) == {"goal", "notes", "current"}


def test_draft_tool_roundtrip_with_fake_completer(monkeypatch):
    fake(monkeypatch)
    result = _call("draft", {"goal": USER_GOAL, "notes": "loop twice, then a final gate decision"})
    payload = _text(result)
    assert payload["doc"] == CANONICAL_DOC
    # the verdict is exactly the green door's outcome
    assert payload["verdict"] == {"ok": True, "issues": []}


def test_draft_tool_missing_goal_is_visible_iserror():
    result = _call("draft", {})
    assert result["isError"] is True
    assert "goal" in result["content"][0]["text"].lower()


def test_draft_tool_model_down_is_visible_iserror(monkeypatch):
    def down():
        raise OSError("connection refused")
    monkeypatch.setattr(draft_mod, "get_completer", down)
    result = _call("draft", {"goal": USER_GOAL})
    assert result["isError"] is True
    assert "unreachable" in result["content"][0]["text"]


def test_validate_tool_green_and_red_are_normal_results():
    green = _text(_call("validate", {"doc": CANONICAL_DOC}))
    assert green == {"ok": True, "issues": []}
    red = _text(_call("validate", {"doc": BAD_DOC}))
    assert red["ok"] is False
    assert red["issues"]
    # but a MISSING doc IS a bad tool call: visible isError, not an RPC error
    missing = _call("validate", {})
    assert missing["isError"] is True
    assert "doc" in missing["content"][0]["text"].lower()


def test_catalog_tool_shape():
    payload = _text(_call("catalog"))
    assert [p["primitive"] for p in payload["primitives"]] == [
        "prompt", "research", "plan", "act", "critic", "gate", "merge", "parallel", "route",
    ]
    assert isinstance(payload["rules"], list) and payload["rules"]


def test_ports_of_tool():
    parallel = _text(_call("ports_of", {"primitive": "parallel"}))
    assert "merged" in parallel["provides"]
    unknown = _call("ports_of", {"primitive": "nope"})
    assert unknown["isError"] is True
    assert "nope" in unknown["content"][0]["text"]


def test_wire_check_tool():
    assert _text(_call("wire_check", {"source": "research", "target": "critic"}))["wireable"] is True
    assert _text(_call("wire_check", {"source": "act", "target": "gate"}))["wireable"] is False
    result = _call("wire_check", {"source": "research"})
    assert result["isError"] is True


# ── JSON-RPC error shapes (the client's _check raises on these) ─────────────


def test_unsupported_method_is_rpc_error():
    resp = mcp_server.handle_message(_request("resources/list", None, 9))
    assert "result" not in resp
    assert resp["error"]["code"] == -32601  # method not found


def test_unknown_tool_is_rpc_error_naming_the_closed_set():
    resp = mcp_server.handle_message(_request("tools/call", {"name": "no_such_tool"}, 10))
    assert "result" not in resp
    assert resp["error"]["code"] == -32602  # invalid params
    assert "no_such_tool" in resp["error"]["message"]
    assert "draft" in resp["error"]["message"]  # the closed set is visible


def test_tools_call_requires_a_name():
    resp = mcp_server.handle_message(_request("tools/call", {}, 11))
    assert "error" in resp and resp["error"]["code"] == -32602


def test_idless_methods_never_answer_and_idless_bad_requests_do():
    assert mcp_server.handle_message({"jsonrpc": "2.0", "method": "anything"}) is None
    resp = mcp_server.handle_message({"jsonrpc": "2.0", "id": 12})  # id, no method
    assert resp["error"]["code"] == -32600  # invalid request


# ── the flagship: THIS repo's own client driving the spawned server ─────────


def test_repo_client_drives_the_real_stdio_server():
    command = [sys.executable, "-m", "design.mcp_server"]
    # offline mode: canned reply through the public set_completer seam
    env = {**os.environ, "GUTS_DESIGN_CANNED_REPLY": json.dumps(CANONICAL_DOC)}
    conn = StdioConnection(command, env=env).connect()
    try:
        tools = conn.list_tools()
        assert [t.name for t in tools] == TOOL_NAMES
        draft_tool = next(t for t in tools if t.name == "draft")
        assert draft_tool.input_schema["required"] == ["goal"]

        payload = json.loads(conn.call_tool("catalog"))
        assert len(payload["primitives"]) == 9 and payload["rules"]

        green = json.loads(conn.call_tool("validate", {"doc": CANONICAL_DOC}))
        assert green == {"ok": True, "issues": []}
        red = json.loads(conn.call_tool("validate", {"doc": BAD_DOC}))
        assert red["ok"] is False and red["issues"]

        # model-backed tool, canned reply — the user's own sentence, end to end
        drafted = json.loads(conn.call_tool("draft", {
            "goal": USER_GOAL, "notes": "loop until the critic passes",
        }))
        assert drafted["doc"] == CANONICAL_DOC
        assert drafted["verdict"]["ok"] is True

        # isError is visible to the client as an McpError, not a crash
        with pytest.raises(McpError, match="goal"):
            conn.call_tool("draft", {})
        with pytest.raises(McpError, match="unknown tool"):
            conn.call_tool("no_such_tool", {})
    finally:
        conn.close()


# ── the http face (same dispatch; client's http/sse path) ───────────────────


def test_http_face_serves_the_same_wire_contract(monkeypatch):
    fake(monkeypatch)
    from fastapi.testclient import TestClient
    client = TestClient(mcp_server.build_app())

    r = client.post("/mcp", json=_request("initialize", {"protocolVersion": "2025-06-18"}, 1))
    assert r.status_code == 200
    assert r.json()["id"] == 1 and r.json()["result"]["protocolVersion"] == "2025-06-18"

    # the client treats any <400 empty-ish ack for a notification as fine
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert r.status_code == 202

    r = client.post("/mcp", json=_request("tools/list", None, 2))
    assert [t["name"] for t in r.json()["result"]["tools"]] == TOOL_NAMES

    r = client.post("/mcp", json=_request("tools/call", {
        "name": "draft", "arguments": {"goal": USER_GOAL}}, 3))
    payload = json.loads(r.json()["result"]["content"][0]["text"])
    assert payload["doc"] == CANONICAL_DOC and payload["verdict"]["ok"] is True

    assert client.get("/health").json()["ok"] is True


def test_canned_env_installs_an_offline_completer(monkeypatch):
    monkeypatch.setenv(mcp_server.CANNED_REPLY_ENV, json.dumps(CANONICAL_DOC))
    assert mcp_server._maybe_install_canned() is True
    payload = _text(_call("draft", {"goal": USER_GOAL}))
    assert payload["doc"] == CANONICAL_DOC and payload["verdict"]["ok"] is True


def test_canned_env_rejects_non_object_docs(monkeypatch):
    monkeypatch.setenv(mcp_server.CANNED_REPLY_ENV, '"a string is not a document"')
    with pytest.raises(SystemExit, match="unusable"):
        mcp_server._maybe_install_canned()


@pytest.fixture(autouse=True)
def _reset_completer():
    yield
    draft_mod.set_completer(None)
