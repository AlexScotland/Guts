"""McpPool: process-shared, alias-keyed connections (docs/agent-graph.md).

An stdio/SSE MCP server must **not** be spawned per turn. The pool owns the
one live connection per alias:

  ``set(alias, spec)``    bind: register a spec (from the agent document)
  ``get(alias)``          the live connection (spawned/opened on first use)
  ``tools(alias)``        the translated tool list — tools/list runs ONCE
                          per spawn, cached until reset
  ``call_tool(alias, tool, args)``   → str (the adapter's back end)
  ``reset(alias)``        drop + re-spawn on the next get (re-activate path)
  ``drain()``             close everything (shutdown / tests)

The factory is injectable so the fake tests pin the lifecycle WITHOUT any
real binary or network (the repo's frozen fakes-only rule); the real
default is ``client.open_connection``.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from .client import McpConnection, McpTool, open_connection


class McpPoolError(KeyError):
    """No spec/connection for an alias — a visible failure, never a silent
    empty toolset (mirrors ComponentAliasError's KeyError lineage)."""


class _Entry:
    __slots__ = ("spec", "conn", "tools")

    def __init__(self, spec: Any):
        self.spec = spec
        self.conn: Optional[McpConnection] = None
        self.tools: Optional[list[McpTool]] = None


class McpPool:
    def __init__(self, factory: Optional[Callable[[Any], McpConnection]] = None):
        self._lock = threading.RLock()
        self._factory = factory or open_connection
        self._entries: dict[str, _Entry] = {}
        self._spawn_count = 0  # every factory call increments — the "no per-turn spawn" pin

    # ── bind ───────────────────────────────────────────────────

    def set(self, alias: str, spec: Any) -> None:
        """Register (or replace) the spec for an alias. Replaces a live
        connection's spec on the NEXT reset — an in-flight turn keeps the
        connection it started with (the pinned snapshot semantics)."""
        if not isinstance(alias, str) or not alias.strip():
            raise McpPoolError(f"alias must be a non-empty string; got {alias!r}")
        with self._lock:
            entry = self._entries.get(alias)
            if entry is None:
                self._entries[alias] = _Entry(spec)
            else:
                entry.spec = spec  # conn/tools stay until reset(alias)

    def has(self, alias: str) -> bool:
        with self._lock:
            return alias in self._entries

    def aliases(self) -> list[str]:
        with self._lock:
            return sorted(self._entries)

    # ── run ────────────────────────────────────────────────────

    def get(self, alias: str) -> McpConnection:
        """The live connection for an alias, created on first use."""
        with self._lock:
            entry = self._entries.get(alias)
            if entry is None:
                raise McpPoolError(
                    f"no MCP spec for alias {alias!r} — register it at bind time (pool.set)"
                )
            if entry.conn is None or not entry.conn.live:
                entry.conn = self._factory(entry.spec)
                self._spawn_count += 1
            return entry.conn

    def tools(self, alias: str) -> list[McpTool]:
        """The translated tool list — tools/list runs once per spawn."""
        with self._lock:
            entry = self._require(alias)
            if entry.tools is None:
                entry.tools = list(self.get(alias).list_tools())
            return list(entry.tools)

    def call_tool(self, alias: str, tool_name: str, args: dict | None = None) -> str:
        with self._lock:
            self._require(alias)
        return self.get(alias).call_tool(tool_name, args)

    # ── lifecycle ──────────────────────────────────────────────

    def reset(self, alias: str) -> None:
        """Drop the live connection + cached tools (the spec stays): the
        re-activate path — next get() re-spawns with the current spec."""
        with self._lock:
            entry = self._entries.get(alias)
            if entry is None:
                return
            if entry.conn is not None:
                try:
                    entry.conn.close()
                except Exception:
                    pass  # teardown best-effort; the entry is dropped anyway
            entry.conn = None
            entry.tools = None

    def drain(self) -> None:
        """Close every connection and drop every entry (shutdown)."""
        with self._lock:
            for entry in self._entries.values():
                if entry.conn is not None:
                    try:
                        entry.conn.close()
                    except Exception:
                        pass
            self._entries.clear()

    def spawn_count(self) -> int:
        """How many times the factory ran (the fake's "no per-turn spawn"
        oracle: N turns == 1 spawn for a stable alias)."""
        with self._lock:
            return self._spawn_count

    # ── internals ──────────────────────────────────────────────

    def _require(self, alias: str) -> _Entry:
        entry = self._entries.get(alias)
        if entry is None:
            raise McpPoolError(f"no MCP spec for alias {alias!r} — register it at bind time (pool.set)")
        return entry
