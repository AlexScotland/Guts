"""app/mcp — the one genuinely new runtime piece (docs/agent-graph.md).

A stdio/SSE MCP server must **not** be spawned per turn: ``pool.py`` holds a
process-shared connection per alias, ``adapter.py`` makes a remote MCP tool
just another ``ToolRegistry`` entry (the executor already iterates it, zero
special-casing), and ``client.py`` is a minimal MCP client — JSON-RPC 2.0
over the transports the v1 vocabulary allows (``stdio``, ``sse``, ``http``) —
stdlib + requests, no SDK dependency.

v1 scope (the doc's closed vocabulary): list tools, call tools. The
connection contract the rest of the package (and the fakes) rely on:
  * ``list_tools() -> list[McpTool]``
  * ``call_tool(name, args) -> str``
  * ``close()`` idempotent; ``live`` property; ``connects`` counter (the
    pool test pins "no per-turn spawn" by reading it).
"""
from .client import (
    HttpConnection,
    McpConnection,
    McpError,
    McpTool,
    SseConnection,
    StdioConnection,
    open_connection,
)
from .adapter import McpAdapterError, McpToolAdapter
from .pool import McpPool, McpPoolError

__all__ = [
    "HttpConnection",
    "McpAdapterError",
    "McpConnection",
    "McpError",
    "McpPool",
    "McpPoolError",
    "McpTool",
    "McpToolAdapter",
    "SseConnection",
    "StdioConnection",
    "open_connection",
]
