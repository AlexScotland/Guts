"""Face 1 — Face 0 as an MCP server, hand-rolled (no SDK).

Same decision the client made (``app/mcp/__init__.py``: "stdlib + requests,
no SDK dependency"). Here it is additionally forced: the official SDK's
PyPI name is ``mcp``, which shadows this repo's own top-level ``app/mcp/``
package (the client, pool, adapter) — so the SDK cannot even be imported
reliably from this codebase. And the wire protocol this face speaks is
already implemented in exactly one direction in this repo
(``app/mcp/client.py``). This module is its mirror image:

  * same handshake   — ``initialize`` then ``notifications/initialized``
  * same methods     — ``tools/list`` / ``tools/call``
  * same shapes      — result ``{tools: [{name, description, inputSchema}]}``;
                       call result ``{content: [{type: "text", text}]}``;
                       ``isError: true`` = a VISIBLE tool failure (NOT a
                       JSON-RPC error — the client surfaces it verbatim);
                       every response echoes the request ``id``;
                       notifications never get a response.

So this repo's own client (``StdioConnection`` / ``open_connection``) is
the integration test for this server.

Tools — the whole Face 0 public surface, same functions the HTTP face
exposes, single source of truth in ``design.draft`` / ``design.catalog``:

  draft(goal, notes?, current?)   ->  {"doc": ..., "verdict": ...}
      Model-backed. An invalid document is a NORMAL result (verdict.ok
      false); only model failure / bad arguments are isError.
  validate(doc)                   ->  {"ok": bool, "issues": [str, ...]}
      The green door (``validate_graph``) as a tool — no model.
  catalog()                       ->  {"primitives": [...], "rules": [...]}
  ports_of(primitive)             ->  one primitive's port shape
  wire_check(source, target)      ->  whether a data edge could carry

Transports (both client paths; the client branches on the response
Content-Type, so a plain-JSON answer satisfies its ``http`` and ``sse``
transports alike):

  stdio — NDJSON JSON-RPC:   python -m design.mcp_server
  http  — POST JSON-RPC:     python -m design.mcp_server --http [PORT=8090]

Offline mode (tests / demos, no model required): set
  GUTS_DESIGN_CANNED_REPLY=<json graph document>
and the server installs a canned completer through the public
``design.draft.set_completer`` seam; ``draft`` then returns that document
with the usual verdict. Nothing else changes.

New dependencies: none. stdlib + the already-pinned fastapi/uvicorn.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import catalog as design_catalog
from . import draft as design_draft

PROTOCOL_VERSION = "2025-06-18"  # same constant as app/mcp/client.py
SERVER_INFO = {"name": "guts-design", "version": "1.0.0"}
CANNED_REPLY_ENV = "GUTS_DESIGN_CANNED_REPLY"
DEFAULT_HTTP_PORT = 8090


# ── failure models (the client's two distinct worlds) ────────────────────────


class ProtocolError(Exception):
    """Protocol-level failure -> JSON-RPC error response (id echoed)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(Exception):
    """Tool-level failure -> result with isError: true (visible, not fatal)."""


def _log(message: str) -> None:
    # stderr is the server's log channel; the stdio client pipes it away
    # untouched, and on http it lands in the service log.
    print(f"[guts-design-mcp] {message}", file=sys.stderr, flush=True)


# ── tool handlers (pure calls into Face 0's public surface) ──────────────────


def _tool_draft(args: dict) -> str:
    try:
        result = design_draft.draft(args.get("goal"), args.get("notes"),
                                    args.get("current"))
    except design_draft.DraftError as exc:
        # Core's own visible messages ("goal is required", "model failed...").
        raise ToolError(str(exc)) from exc
    except OSError as exc:  # get_completer / socket-level, outside the core
        raise ToolError(f"design model unreachable: {exc}") from exc
    return json.dumps({"doc": result.doc, "verdict": result.verdict}, indent=2)


def _tool_validate(args: dict) -> str:
    doc = args.get("doc")
    if not isinstance(doc, dict):
        raise ToolError("validate requires a graph document (object) at 'doc'")
    return json.dumps(design_draft.verdict(doc))


def _tool_catalog(args: dict) -> str:
    return json.dumps(
        {"primitives": design_catalog.list_primitives(),
         "rules": design_catalog.RULES},
        indent=2,
    )


def _tool_ports_of(args: dict) -> str:
    primitive = args.get("primitive")
    if not isinstance(primitive, str) or not primitive:
        raise ToolError("ports_of requires a primitive name (string)")
    try:
        shape = design_catalog.ports_of(primitive)
    except ValueError as exc:  # unknown primitive — visible, not fatal
        raise ToolError(str(exc)) from exc
    return json.dumps(shape, indent=2)


def _tool_wire_check(args: dict) -> str:
    source, target = args.get("source"), args.get("target")
    if not isinstance(source, str) or not isinstance(target, str):
        raise ToolError("wire_check requires source and target primitive names")
    wireable = design_catalog.wire_check(source, target)
    return json.dumps({"source": source, "target": target, "wireable": wireable})


# ── the tool table (tools/list descriptors + dispatch registry) ──────────────

# (name, description, inputSchema, handler)
_TOOLS: list[tuple[str, str, dict[str, Any], Callable[[dict], str]]] = [
    (
        "draft",
        "Propose a GUTS graph document for a goal (model-backed). Returns "
        "{\"doc\": <graph document>, \"verdict\": {ok, issues}}. A red "
        "verdict is a NORMAL result; only model failure is an error. notes "
        "carry the business-case intent (loop policy etc.); current is an "
        "optional document to re-draft.",
        {
            "type": "object",
            "properties": {
                "goal": {"type": "string"},
                "notes": {"type": "string"},
                "current": {"type": "object"},
            },
            "required": ["goal"],
        },
        _tool_draft,
    ),
    (
        "validate",
        "Run the green door (validate_graph) on a document. Returns "
        "{\"ok\": bool, \"issues\": [str]} — no model, no mutation.",
        {
            "type": "object",
            "properties": {"doc": {"type": "object"}},
            "required": ["doc"],
        },
        _tool_validate,
    ),
    (
        "catalog",
        "The frozen graph language: the 9 primitives (role, description, "
        "ports, config keys, loopable) plus the wiring RULES. No arguments.",
        {"type": "object", "properties": {}},
        _tool_catalog,
    ),
    (
        "ports_of",
        "One primitive's port shape: provides / accepts / loopback ports.",
        {
            "type": "object",
            "properties": {"primitive": {"type": "string"}},
            "required": ["primitive"],
        },
        _tool_ports_of,
    ),
    (
        "wire_check",
        "Whether a data edge source->target could carry (shared type or one "
        "side any/all). Returns {source, target, wireable}.",
        {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
            },
            "required": ["source", "target"],
        },
        _tool_wire_check,
    ),
]

_HANDLERS: dict[str, Callable[[dict], str]] = {name: handler for name, _, _, handler in _TOOLS}


def tools_list_result() -> dict:
    return {
        "tools": [
            {"name": name, "description": description, "inputSchema": schema}
            for name, description, schema, _ in _TOOLS
        ]
    }


# ── dispatch (transport-agnostic: one message in, one response out) ─────────


def _content(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _dispatch_call(params: Any) -> dict:
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise ProtocolError(-32602, "tools/call requires params.name")
    name = params["name"]
    handler = _HANDLERS.get(name)
    if handler is None:
        known = ", ".join(_HANDLERS)
        raise ProtocolError(
            -32602, f"unknown tool {name!r} — the closed set is: {known}")
    arguments = params.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        raise ProtocolError(-32602, "tools/call arguments must be an object")
    try:
        return _content(handler(arguments if isinstance(arguments, dict) else {}))
    except ToolError as exc:
        _log(f"tool {name!r}: {exc}")
        return {"isError": True, **_content(str(exc))}
    except Exception as exc:  # a bug must never kill the protocol
        _log(f"tool {name!r} crashed: {exc!r}")
        return {"isError": True, **_content(f"internal error: {exc}")}


def handle_message(msg: Any) -> dict | None:
    """One incoming JSON-RPC message -> the response, or None.

    The client matches on ``id``: every REQUEST response echoes the
    request's id; NOTIFICATIONS (no id) never get one (and JSON-RPC says
    a server must never answer a notification).
    """
    if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
        if isinstance(msg, dict) and "id" in msg:
            return {"jsonrpc": "2.0", "id": msg.get("id"),
                    "error": {"code": -32600, "message": "invalid request"}}
        return None

    method = msg["method"]
    if "id" not in msg:
        return None  # any notification: consume silently

    response: dict[str, Any] = {"jsonrpc": "2.0", "id": msg.get("id")}
    try:
        if method == "initialize":
            response["result"] = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": dict(SERVER_INFO),
            }
        elif method == "tools/list":
            response["result"] = tools_list_result()
        elif method == "tools/call":
            response["result"] = _dispatch_call(msg.get("params"))
        else:
            response["error"] = {"code": -32601,
                                 "message": f"method not found: {method}"}
    except ProtocolError as exc:
        response["error"] = {"code": exc.code, "message": exc.message}
    return response


# ── offline mode (the public set_completer seam, injected at startup) ───────


class _CannedCompleter:
    """Returns the same canned document for every call — no model."""

    def __init__(self, doc: dict) -> None:
        self.doc = doc

    def invoke(self, messages: Any) -> str:
        return json.dumps(self.doc, indent=2)


def _maybe_install_canned() -> bool:
    raw = os.environ.get(CANNED_REPLY_ENV, "").strip()
    if not raw:
        return False
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(f"{CANNED_REPLY_ENV} is set but unusable: {exc}")
    design_draft.set_completer(_CannedCompleter(doc))
    _log(f"canned reply installed (offline mode) — {CANNED_REPLY_ENV}")
    return True


# ── transports ────────────────────────────────────────────────────────────────


def serve_stdio() -> int:
    """NDJSON JSON-RPC loop on stdin/stdout (the client's stdio path).

    Blocks per line until the client writes it (its reader thread pumps
    the reply by id); EOF ends the server.
    """
    _maybe_install_canned()
    out = sys.stdout

    def write_response(obj: dict) -> None:
        out.write(json.dumps(obj) + "\n")
        out.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            write_response({"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700,
                                      "message": f"parse error: {exc.msg}"}})
            continue
        response = handle_message(msg)
        if response is not None:
            write_response(response)
    return 0


def build_app():
    """FastAPI face over the same dispatch (the client's http/sse path).

    Plain-JSON answers (the client branches on the response Content-Type,
    so this satisfies both its ``http`` and ``sse`` transports); a POST
    with no id is a notification — 202, no body. Served at /mcp (and /).
    """
    _maybe_install_canned()
    app = FastAPI(title="guts-design MCP (Face 1)")

    # CORS mirrors app/server.py: cross-origin setups (Pages export, a page
    # served from another origin dialling this service by absolute URL) must
    # be able to POST /mcp directly. Same-origin proxy setups never see it.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    async def _handle(request: Request):
        try:
            msg = await request.json()
        except Exception:
            return JSONResponse(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": -32700, "message": "parse error"}},
                status_code=200)
        response = handle_message(msg)
        if response is None:
            return JSONResponse(None, status_code=202)
        return JSONResponse(response)

    app.post("/mcp")(_handle)
    app.post("/")(_handle)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "service": SERVER_INFO["name"]}

    return app


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "-h" in args or "--help" in args:
        print(__doc__)
        return 0
    if "--http" in args:
        rest = [a for a in args if a != "--http"]
        port = DEFAULT_HTTP_PORT
        if rest:
            try:
                port = int(rest[0])
            except ValueError:
                _log(f"port {rest[0]!r} is not an integer — using {port}")
        import uvicorn

        uvicorn.run(build_app(), host="0.0.0.0", port=port, log_level="warning")
        return 0
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
