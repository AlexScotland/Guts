import inspect
import logging

from langchain_core.tools import BaseTool
from tools.tool_call import ToolCall

logger = logging.getLogger(__name__)


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def register_many(self, tools: list[BaseTool]):
        for tool in tools:
            self.register(tool)

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list(self):
        return list(self._tools.values())

    def names(self):
        return list(self._tools.keys())

    @staticmethod
    def _accepted_params(tool: BaseTool) -> set[str] | None:
        """Param names the underlying function will accept (or None if unknown)."""
        fn = getattr(tool, "tool", None)
        if fn is None:
            fn = tool
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return None
        return {
            name
            for name, p in sig.parameters.items()
            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        }

    @classmethod
    def _clean_args(cls, tool: BaseTool, raw) -> dict:
        """Flatten whatever the provider sent down to the keys the function wants.

        Handles a top-level dict, args nested under an envelope key like 'v__args',
        a nested dict, or a list, without us having to know the exact shape.
        """
        accepted = cls._accepted_params(tool)
        if accepted is None:
            return raw if isinstance(raw, dict) else {}

        flat: dict = {}

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in accepted and k not in flat:
                        flat[k] = v
                    else:
                        walk(v)
            elif isinstance(node, (list, tuple)):
                for item in node:
                    walk(item)

        walk(raw)
        return flat

    def execute(self, tool_call: ToolCall):
        tool = self.get(tool_call.name)
        if tool is None:
            raise ValueError(f"Unknown tool: {tool_call.name}")

        raw = tool_call.args
        args = self._clean_args(tool, raw)

        # One INFO line so we can SEE the real provider shape if it ever changes.
        logger.info("TOOLCALL %s: raw=%r -> clean=%r", tool_call.name, raw, args)

        return tool.invoke(args)