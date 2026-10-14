"""McpToolAdapter: a remote MCP tool as a harness ``ToolRegistry`` entry.

The doc's call path, verbatim — no special-casing anywhere downstream:

    agent turn → executor (LLM) picks a tool → ToolRegistry.execute(tool_call)
               → McpToolAdapter.invoke(args) → McpPool.call_tool(alias, name, args)
               → McpConnection.call_tool(...) → return string

The adapter carries exactly three facts (alias, remote tool name, model
description) and a handle to the pool. It keeps NO connection of its own,
so a re-activation (pool reset with a new spec) is transparent: every call
resolves the LIVE connection through the pool.
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.tools import BaseTool

#: Provider-side envelopes we know how to unwrap (ToolRegistry._clean_args
#: handles the same shapes for local tools; the adapter's raw args pass
#: through untouched, so it unwraps the shapes itself).
_ENVELOPE_KEYS = ("v__args", "args", "input", "arguments", "parameters")


def _normalize_args(raw: Any) -> dict:
    """Flatten the provider's argument envelope down to a plain dict.

    ``None``/scalar → {}, a dict passes (single-key envelope unwrapped),
    a list takes its first dict entry.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        if len(raw) == 1:
            only_key, only_val = next(iter(raw.items()))
            if isinstance(only_val, dict) and only_key in _ENVELOPE_KEYS:
                return dict(only_val)
        return dict(raw)
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                return _normalize_args(item)
        return {}
    if isinstance(raw, str):
        return {"text": raw}
    return {}


class McpToolAdapter(BaseTool):
    name: str = "mcp_tool"
    description: str = ""
    alias: str = ""        # the pool key (the mcp[] entry's alias)
    tool_name: str = ""    # the REMOTE tool name (from tools/list)
    pool: Optional[Any] = None  # a McpPool (or fake) — Any: no schema leakage

    def _to_args_and_kwargs(self, tool_input: Any, tool_call_id: Optional[str] = None):
        # BaseTool refuses list inputs; the executor may hand us one (a
        # provider envelope), so flatten it to a single dict ourselves.
        if isinstance(tool_input, (list, tuple)):
            return (), dict(_normalize_args(list(tool_input)))
        return super()._to_args_and_kwargs(tool_input, tool_call_id)

    def _run(self, **kwargs: Any) -> str:
        if self.pool is None:
            raise McpAdapterError("adapter has no pool — it was not built by/for a pool entry")
        if not self.alias or not self.tool_name:
            raise McpAdapterError(f"adapter is missing alias/tool_name ({self.alias!r}/{self.tool_name!r})")
        return self.pool.call_tool(self.alias, self.tool_name, _normalize_args(kwargs or None))


class McpAdapterError(RuntimeError):
    """A mis-built adapter (bind-time misconfiguration, run-visible)."""
