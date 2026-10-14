"""A minimal MCP client (JSON-RPC 2.0) — stdlib + requests, no SDK.

v1 scope (the doc's closed transports ``stdio``/``sse``/``http``):
  * ``stdio`` — newline-delimited JSON-RPC on a spawned server's stdin/stdout;
  * ``sse``   — POST a JSON-RPC message to the endpoint; a
               ``text/event-stream`` response is read for its message frame;
  * ``http``  — the same POST, plain JSON response.

Lifecycle: ``connect()`` runs the MCP handshake exactly once per connection
(spawn + ``initialize`` + ``notifications/initialized``); ``list_tools`` and
``call_tool`` then run against the live connection; ``close()`` tears it down.
The pool (pool.py) owns which connections exist; the bind path calls
``connect()`` and treats any raised error as a VISIBLE bind failure — never
a silent empty toolset (the doc's failure model).

NOT built (the doc's out-of-scope list): the server-to-client half (SSE
subscriptions, server-initiated requests), result streaming. The harness is
request/response — list tools, call tool.
"""
from __future__ import annotations

import itertools
import json
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

import requests

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "learning-ai-harness", "version": "1.0.0"}
DEFAULT_TIMEOUT = 30.0


class McpError(Exception):
    """A failed MCP handshake, call, or transport step (bind- or run-visible)."""


@dataclass(frozen=True)
class McpTool:
    """One remote tool as reported by tools/list (already translated)."""

    name: str
    description: str = ""
    input_schema: dict | None = None


class McpConnection:
    """One live MCP connection: the contract the pool and adapter rely on.

    ``connects`` counts successful handshakes — the pool's "no per-turn
    spawn" test reads it (a fresh connection per turn would increment it)."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout
        # RLock: the handshake and each request run under this lock AND the
        # per-id condition (built on the same lock, held by the reader thread)
        # — a plain Lock would deadlock connect() -> _await().
        self._lock = threading.RLock()
        self.connects = 0
        self._ids_it = itertools.count(1)

    @property
    def live(self) -> bool:
        raise NotImplementedError

    @property
    def connected(self) -> bool:
        raise NotImplementedError

    def connect(self) -> "McpConnection":
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError

    # ── shared wire helpers ─────────────────────────────────────

    def _request_msg(self, method: str, params: dict | None, request_id) -> dict:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": request_id}
        if params is not None:
            msg["params"] = params
        return msg

    @staticmethod
    def _notify_msg() -> dict:
        return {"jsonrpc": "2.0", "method": "notifications/initialized"}

    @classmethod
    def _check(cls, response: Any, request_id) -> Any:
        if not isinstance(response, dict):
            raise McpError(f"MCP response for id {request_id} is not an object: {response!r}")
        if "error" in response and response["error"]:
            err = response["error"]
            detail = err.get("message") if isinstance(err, dict) else err
            raise McpError(f"MCP request {request_id} failed: {detail}")
        if "result" in response:
            return response["result"]
        raise McpError(f"MCP response for id {request_id} has no result: {response!r}")

    @staticmethod
    def _tool_payloads(result: Any) -> list[dict]:
        tools = result.get("tools") if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise McpError(f"tools/list result has no tools list: {result!r}")
        return [t for t in tools if isinstance(t, dict) and t.get("name")]

    @staticmethod
    def _translate(payload: dict) -> McpTool:
        schema = payload.get("inputSchema")
        if not isinstance(schema, dict):
            schema = None
        return McpTool(
            name=str(payload["name"]),
            description=str(payload.get("description") or ""),
            input_schema=schema,
        )

    @staticmethod
    def _call_text(result: Any) -> str:
        """A tools/call result is {content: [{type:text, text}, ...],
        isError?}. isError=true is a VISIBLE call failure, not a string."""
        if not isinstance(result, dict):
            raise McpError(f"tools/call result is not an object: {result!r}")
        if result.get("isError"):
            content = result.get("content")
            text = (
                content[0].get("text")
                if isinstance(content, list) and content and isinstance(content[0], dict)
                else None
            )
            raise McpError(f"remote tool returned an error: {text if text is not None else result!r}")
        content = result.get("content")
        if not isinstance(content, list) or not content:
            raise McpError(f"tools/call result has no content: {result!r}")
        parts = [
            str(item["text"])
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and "text" in item
        ]
        if not parts:
            raise McpError(f"tools/call result content is not text: {result!r}")
        return "\n".join(parts)

    # ── public contract (raise when not connected) ──────────────

    def list_tools(self) -> list[McpTool]:
        with self._lock:
            if not self.connected:
                raise McpError("connection is not connected — call connect() (or reset the pool entry)")
            request_id = next(self._ids_it)
            result = self._request("tools/list", None, request_id)
        return [self._translate(p) for p in self._tool_payloads(result)]

    def call_tool(self, name: str, args: dict | None = None) -> str:
        with self._lock:
            if not self.connected:
                raise McpError("connection is not connected — call connect() (or reset the pool entry)")
            request_id = next(self._ids_it)
            params = {"name": name}
            if args:
                params["arguments"] = args
            result = self._request("tools/call", params, request_id)
        return self._call_text(result)

    def _request(self, method: str, params: dict | None, request_id) -> Any:
        """One request/response round trip (transport-specific)."""
        raise NotImplementedError


# ── stdio transport ─────────────────────────────────────────────────────────


class StdioConnection(McpConnection):
    """Newline-delimited JSON-RPC on a spawned MCP server (argv ``command``).

    One reader thread pumps stdout into per-id slots, so server-to-client
    chatter between our replies resolves harmlessly."""

    def __init__(self, command: list[str], env: dict | None = None, timeout: float = DEFAULT_TIMEOUT):
        super().__init__(timeout=timeout)
        if not command:
            raise McpError("stdio MCP entry has no command")
        self.command = tuple(command)
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._ids_it = None
        self._pending: dict[int, Any] = {}
        self._cond = threading.Condition(self._lock)
        self._reader: threading.Thread | None = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and self._proc is not None and self._proc.poll() is None

    @property
    def live(self) -> bool:
        return self.connected

    def connect(self) -> "StdioConnection":
        with self._lock:
            if self._connected:
                return self
            proc = subprocess.Popen(
                list(self.command),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
            )
            self._proc = proc
            self._ids_it = itertools.count(1)
            self._reader = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader.start()
            # Handshake (id comes from the live counter — the reader may be
            # delivering server chatter while we wait).
            request_id = next(self._ids_it)
            self._pending[request_id] = None
            self._send(self._request_msg(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
                request_id,
            ))
            response = self._await(request_id)
            self._send(self._notify_msg())
            self._connected = True
            self.connects += 1
            return self

    # ── plumbing under the shared lock ──────────────────────────

    def _reader_loop(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        while True:
            line = self._proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                continue  # non-JSON chatter stays inert
            rid = msg.get("id") if isinstance(msg, dict) else None
            if isinstance(rid, int):
                with self._cond:
                    if rid in self._pending:
                        self._pending[rid] = msg
                        self._cond.notify_all()

    def _send(self, msg: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise McpError("stdio server is not running")
        try:
            self._proc.stdin.write((json.dumps(msg) + "\n").encode("utf-8"))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"stdio MCP server died: {exc}") from exc

    def _await(self, request_id: int) -> Any:
        with self._cond:
            end = time.monotonic() + self.timeout
            while self._pending.get(request_id) is None:
                remaining = end - time.monotonic()
                if remaining <= 0:
                    raise McpError(f"MCP request {request_id} timed out")
                self._cond.wait(min(remaining, 0.5))
            return self._pending.pop(request_id)

    def _request(self, method: str, params: dict | None, request_id) -> Any:
        # Seed the id BEFORE sending: the reader delivers by id and silently
        # ignores ids it does not know (server chatter / out-of-band results),
        # so an unseeded request would never resolve (pinned by the
        # connect-then-list test).
        self._pending[request_id] = None
        self._send(self._request_msg(method, params, request_id))
        return self._check(self._await(request_id), request_id)

    def close(self) -> None:
        with self._lock:
            if self._connected or self._proc is not None:
                self._connected = False
                self._cond.notify_all()
                proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ── url transports (sse / http) ─────────────────────────────────────────────


def _sse_extract(response: requests.Response) -> Any:
    """Pull the frame out of a text/event-stream body: the data: lines of
    one (or the last) event, reassembled and parsed as JSON."""
    data: list[str] = []
    for line in (response.text or "").splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data.append(line[len("data:"):].strip())
        elif line == "" and data:
            break  # blank line ends the event
    if not data:
        raise McpError("MCP SSE response carried no data frame")
    try:
        return json.loads("\n".join(data))
    except json.JSONDecodeError as exc:
        raise McpError(f"MCP SSE data frame is not JSON: {data!r}") from exc


class _UrlConnection(McpConnection):
    def __init__(self, url: str, headers: dict | None = None, timeout: float = DEFAULT_TIMEOUT):
        super().__init__(timeout=timeout)
        if not url:
            raise McpError(f"{self.transport} MCP entry has no url")
        self.url = url
        # MCP (Streamable HTTP) requires the client to accept both possible
        # response shapes — plain JSON and an SSE stream — or strict gateways
        # answer the first `initialize` POST with 406.
        self._headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **(headers or {}),
        }
        self._ids_it = itertools.count(1)
        self._connected = False

    transport = "http"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def live(self) -> bool:
        return self._connected

    def _post(self, msg: dict) -> Any:
        try:
            resp = requests.post(self.url, json=msg, headers=self._headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise McpError(f"MCP endpoint {self.url} unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise McpError(f"MCP endpoint {self.url} returned {resp.status_code}: {resp.text[:200]}")
        if not resp.content:
            return None  # an empty ack (notifications/initialized), not a result
        ctype = (resp.headers.get("Content-Type") or "").lower()
        # The response shape is the SERVER's choice (JSON or SSE), not the
        # declared transport — branch on the actual Content-Type only.
        if "text/event-stream" in ctype:
            return _sse_extract(resp)
        try:
            return resp.json()
        except ValueError as exc:
            raise McpError(f"MCP endpoint returned non-JSON response: {resp.text[:200]}") from exc

    def connect(self) -> "_UrlConnection":
        with self._lock:
            if self._connected:
                return self
            request_id = next(self._ids_it)
            response = self._post(self._request_msg(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": CLIENT_INFO,
                },
                request_id,
            ))
            self._check(response, request_id)
            self._post(self._notify_msg())
            self._connected = True
            self.connects += 1
            return self

    def _request(self, method: str, params: dict | None, request_id) -> Any:
        return self._check(self._post(self._request_msg(method, params, request_id)), request_id)

    def close(self) -> None:
        with self._lock:
            self._connected = False


class HttpConnection(_UrlConnection):
    transport = "http"


class SseConnection(_UrlConnection):
    transport = "sse"


# ── factory (the bind path calls this) ─────────────────────────────────────


def open_connection(spec) -> McpConnection:
    """Translate one validated mcp[] entry (schemas.agent.McpSpec or an
    equivalent dict) into a CONNECTED connection. Raises on failure —
    the bind path surfaces that as a visible bind error."""
    transport = getattr(spec, "transport", None)
    if transport is None and isinstance(spec, dict):
        transport = spec.get("transport")
    if transport == "stdio":
        command = getattr(spec, "command", None)
        if command is None and isinstance(spec, dict):
            command = spec.get("command")
        return StdioConnection(list(command or [])).connect()
    if transport in ("sse", "http"):
        url = getattr(spec, "url", None)
        if url is None and isinstance(spec, dict):
            url = spec.get("url")
        headers = getattr(spec, "headers", None)
        if headers is None and isinstance(spec, dict):
            headers = spec.get("headers")
        cls = SseConnection if transport == "sse" else HttpConnection
        return cls(url, headers=headers).connect()
    raise McpError(f"unknown MCP transport {transport!r}")
