from dataclasses import dataclass, field
from typing import Any

@dataclass
class ToolCall:
    """
        A class that represents the invocation of a tool with specific arguments.
    """
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)
