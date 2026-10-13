"""Shared services passed to component factories at construction time.

Any component that needs a cross-cutting dependency (LLM, tool registry,
configuration, ...) receives it here rather than by hardwired import, so a
component is self-contained: give it a context and it can be built anywhere.
"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ComponentContext:
    llm: Any = None            # an LLMProvider (e.g. OllamaProvider)
    tools: Any = None          # a ToolRegistry
    config: Any = None         # an AgentConfig
    extras: dict = field(default_factory=dict)  # preset knobs, e.g. memory_max_messages
