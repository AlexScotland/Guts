"""Wire + lifecycle tests for the MCP layer (v1, docs/agent-graph.md).

Three fake-server flavors, no real binary, no external network (the repo's
frozen fakes-only rule):
  * a stdio MCP server — a python script speaking newline-delimited
    JSON-RPC 2.0 (initialize / tools/list / tools/call), spawned by the
    REAL StdioConnection;
  * an http endpoint — an in-thread ThreadingHTTPServer answering JSON-RPC;
  * an sse endpoint — the same handler serving text/event-stream bodies.

Pinned behaviors (the doc's "Pool" + "Bind then run" test groups):
  * get(alias) reuses a live connection — a fresh spawn only on first use;
  * N turns == 1 spawn (the fake counts its connects);
  * reset(alias) drops and re-spawns (the re-activate path);
  * tools are translated once per spawn (tools/list called once);
  * unknown alias / unknown transport = a visible McpPoolError/McpError —
    never a silent empty toolset;
  * the adapter sits in the real ToolRegistry and the executor's
    ToolRegistry.execute path calls the remote tool with zero special-casing;
  * a remote isError result is a visible McpError, not a string.

Run from the ``app/`` directory:
    .venv-test/bin/pytest tests/test_mcp.py -v
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from mcp import McpAdapterError, McpError, McpPool, McpPoolError, McpToolAdapter, open_connection
from mcp.client import McpTool


# ── fake stdio MCP server (the spawn under test is real python, nothing else) ──

STDIO_SERVER = r'''
import json, sys

def send(msg):
    print(json.dumps(msg), flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "fake-mcp"},
            "capabilities": {},
        }})
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": "echo", "description": "echo its args",
             "inputSchema": {"type": "object", "properties": {"x": {"type": "integer"}}}},
            {"name": "boom", "description": "always fails"},
        ]}})
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments") or {}
        if name == "boom":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": "remote failure"}],
            }})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "echo: " + json.dumps(args)}],
            }})
'''


@pytest.fixture()
def stdio_server(tmp_path):
    script = tmp_path / "fake_mcp_stdio.py"
    script.write_text(STDIO_SERVER, encoding="utf-8")
    return [sys.executable, str(script)]


def _stdio_spec(command, **extra):
    spec = {"transport": "stdio", "command": command}
    spec.update(extra)
    return spec


def test_stdio_connect_lists_tools_and_calls(stdio_server):
    conn = open_connection(_stdio_spec(stdio_server))
    try:
        tools = conn.list_tools()
        assert [t.name for t in tools] == ["echo", "boom"]
        echo = next(t for t in tools if t.name == "echo")
        assert echo.description == "echo its args"
        assert echo.input_schema["properties"]["x"] == {"type": "integer"}
        assert conn.call_tool("echo", {"x": 7}) == "echo: {\"x\": 7}"
    finally:
        conn.close()


def test_stdio_remote_iserror_is_visible(stdio_server):
    conn = open_connection(_stdio_spec(stdio_server))
    try:
        with pytest.raises(McpError, match="remote failure"):
            conn.call_tool("boom", {})
    finally:
        conn.close()


def test_stdio_closed_connection_refuses_work(stdio_server):
    conn = open_connection(_stdio_spec(stdio_server))
    conn.close()
    with pytest.raises(McpError):
        conn.list_tools()


# ── fake http/sse endpoints (in-thread, no external network) ────────────────


class _JsonRpcHandler(BaseHTTPRequestHandler):
    MODE = "json"  # overridden per-test via the server attribute

    def log_message(self, *args):
        pass

    def _dispatch(self, msg: dict):
        method, rid = msg.get("method"), msg.get("id")
        if method == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "fake-http"},
                "capabilities": {},
            }}
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": "echo", "description": "echo its args",
                 "inputSchema": {"type": "object"}},
            ]}}
        if method == "tools/call":
            args = msg["params"].get("arguments") or {}
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "http-echo: " + json.dumps(args)}],
            }}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"no such method {method!r}"}}

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8")
        msg = json.loads(body) if body else {}
        response = self._dispatch(msg)
        if response is None:
            self.send_response(202)
            self.end_headers()
            return
        payload = json.dumps(response)
        if self.server.MODE == "sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(f"event: message\ndata: {payload}\n\n".encode("utf-8"))
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload.encode("utf-8"))


@pytest.fixture()
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _JsonRpcHandler)
    server.MODE = "json"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_http_connect_lists_and_calls(http_server):
    _, port = http_server.server_address
    conn = open_connection({"transport": "http", "url": f"http://127.0.0.1:{port}/mcp"})
    try:
        assert [t.name for t in conn.list_tools()] == ["echo"]
        assert conn.call_tool("echo", {"y": 1}) == "http-echo: {\"y\": 1}"
    finally:
        conn.close()


def test_sse_body_is_read(http_server):
    http_server.MODE = "sse"
    _, port = http_server.server_address
    conn = open_connection({"transport": "sse", "url": f"http://127.0.0.1:{port}/mcp"})
    try:
        assert [t.name for t in conn.list_tools()] == ["echo"]
        assert conn.call_tool("echo", {"z": 2}) == "http-echo: {\"z\": 2}"
    finally:
        conn.close()


def test_failed_endpoint_is_a_visible_connect_failure(http_server):
    # A dead port: the connect handshake must RAISE (the bind failure model),
    # not resolve to a silent empty toolset.
    with pytest.raises(McpError):
        open_connection({"transport": "http", "url": "http://127.0.0.1:1/mcp"})


def test_unknown_transport_rejected(stdio_server):
    with pytest.raises(McpError, match="unknown MCP transport"):
        open_connection({"transport": "grpc", "url": "http://x"})


def test_missing_url_rejected():
    with pytest.raises(McpError, match="no url"):
        open_connection({"transport": "sse"})


# ── pool lifecycle (the doc's pins) ─────────────────────────────────────────


class FakeMcpConnection:
    """Implements the McpConnection contract with counters."""

    instances = 0

    def __init__(self, spec):
        self.spec = spec
        self.connects = 1
        self.closed = False
        self.list_calls = 0
        self.calls = []
        FakeMcpConnection.instances += 1

    @property
    def live(self):
        return not self.closed

    @property
    def connected(self):
        return not self.closed

    def connect(self):
        return self

    def close(self):
        self.closed = True

    def list_tools(self):
        self.list_calls += 1
        return [McpTool("t1", "fake tool", None)]

    def call_tool(self, name, args=None):
        self.calls.append((name, args))
        return f"fake:{name}:{json.dumps(args or {})}"


@pytest.fixture()
def fake_pool():
    FakeMcpConnection.instances = 0
    pool = McpPool(factory=lambda spec: FakeMcpConnection(spec))
    yield pool
    pool.drain()


def test_get_reuses_live_connection(fake_pool):
    fake_pool.set("web", {"transport": "sse", "url": "http://fake"})
    c1 = fake_pool.get("web")
    c2 = fake_pool.get("web")
    assert c1 is c2
    assert c1.connects == 1
    assert fake_pool.spawn_count() == 1


def test_no_per_turn_spawn(fake_pool):
    fake_pool.set("web", {"transport": "sse", "url": "http://fake"})
    for i in range(5):  # five "turns"
        fake_pool.call_tool("web", "t1", {"i": i})
    conn = fake_pool.get("web")
    assert conn.connects == 1
    assert fake_pool.spawn_count() == 1
    assert len(conn.calls) == 5


def test_reset_drops_and_respawns(fake_pool):
    fake_pool.set("web", {"transport": "sse", "url": "http://fake"})
    c1 = fake_pool.get("web")
    fake_pool.reset("web")
    assert c1.closed is True
    c2 = fake_pool.get("web")
    assert c2 is not c1
    assert fake_pool.spawn_count() == 2


def test_reset_applies_new_spec(fake_pool):
    fake_pool.set("web", {"transport": "sse", "url": "http://old"})
    c1 = fake_pool.get("web")
    fake_pool.set("web", {"transport": "stdio", "command": ["python", "-m", "new"]})
    fake_pool.reset("web")
    c2 = fake_pool.get("web")
    assert c1.spec["url"] == "http://old"
    assert c2.spec["command"] == ["python", "-m", "new"]


def test_tools_translated_once_per_spawn(fake_pool):
    fake_pool.set("web", {"transport": "sse", "url": "http://fake"})
    tools1 = fake_pool.tools("web")
    tools2 = fake_pool.tools("web")
    assert [t.name for t in tools1] == ["t1"] and tools1 == tools2
    conn = fake_pool.get("web")
    assert conn.list_calls == 1  # tools/list ran ONCE across both reads
    fake_pool.reset("web")
    fake_pool.tools("web")
    assert fake_pool.get("web").list_calls == 1  # fresh spawn, fresh listing


def test_unknown_alias_is_visible(fake_pool):
    with pytest.raises(McpPoolError, match="no MCP spec"):
        fake_pool.get("ghost")
    with pytest.raises(McpPoolError, match="no MCP spec"):
        fake_pool.call_tool("ghost", "t1")
    assert issubclass(McpPoolError, KeyError)


def test_drain_closes_and_forgets(fake_pool):
    fake_pool.set("web", {"transport": "sse", "url": "http://fake"})
    c1 = fake_pool.get("web")
    fake_pool.drain()
    assert c1.closed is True
    assert fake_pool.aliases() == []
    with pytest.raises(McpPoolError):
        fake_pool.get("web")


# ── adapter: the executor path with zero special-casing ─────────────────────


def _adapter_pool():
    pool = McpPool(factory=lambda spec: FakeMcpConnection(spec))
    pool.set("web", {"transport": "sse", "url": "http://fake"})
    return pool


def test_adapter_invokes_through_pool():
    pool = _adapter_pool()
    adapter = McpToolAdapter(
        name="web_echo", description="echo tool served by the web MCP", alias="web",
        tool_name="t1", pool=pool,
    )
    assert adapter.invoke({"x": 3}) == "fake:t1:{\"x\": 3}"
    pool.drain()


def test_adapter_unwraps_provider_envelope():
    pool = _adapter_pool()
    adapter = McpToolAdapter(
        name="web_echo", description="d", alias="web", tool_name="t1", pool=pool,
    )
    # the v__args envelope shape ToolRegistry._clean_args knows
    assert adapter.invoke({"v__args": {"x": 4}}) == "fake:t1:{\"x\": 4}"
    # a list envelope takes the first dict
    assert adapter.invoke([{"x": 5}]) == "fake:t1:{\"x\": 5}"
    pool.drain()


def test_adapter_without_pool_fails_visibly():
    adapter = McpToolAdapter(name="x", description="d", alias="web", tool_name="t1", pool=None)
    with pytest.raises(McpAdapterError, match="no pool"):
        adapter.invoke({"a": 1})


def test_adapter_in_real_tool_registry_via_executor_path():
    """The doc's exact call path: ToolRegistry.execute → tool.invoke → pool.

    _clean_args finds no local signature on the adapter (no ``.tool``
    function), so the raw args pass through and the adapter unwraps them —
    the remote side sees the right keys either way."""
    from tools.tool_call import ToolCall
    from tools.tool_registry import ToolRegistry

    pool = _adapter_pool()
    adapter = McpToolAdapter(
        name="web_echo", description="d", alias="web", tool_name="t1", pool=pool,
    )
    registry = ToolRegistry()
    registry.register(adapter)
    assert "web_echo" in registry.names()
    assert registry.execute(ToolCall(id="c1", name="web_echo", args={"x": 9})) == "fake:t1:{\"x\": 9}"
    assert registry.execute(
        ToolCall(id="c2", name="web_echo", args={"v__args": {"x": 10}})
    ) == "fake:t1:{\"x\": 10}"
    pool.drain()


def test_reactivation_is_transparent_to_adapter():
    """A pool reset with a new spec must not leave stale connections behind
    on the adapter (the re-activate path)."""
    pool = _adapter_pool()
    first = pool.get("web")
    adapter = McpToolAdapter(
        name="web_echo", description="d", alias="web", tool_name="t1", pool=pool,
    )
    assert pool.call_tool("web", "t1", {"a": 1}).startswith("fake:t1")
    pool.reset("web")
    second = pool.get("web")
    assert second is not first and first.closed is True
    assert adapter.invoke({"b": 2}) == "fake:t1:{\"b\": 2}"
    pool.drain()
