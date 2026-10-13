import json

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    success_criteria: str = "Task completed successfully"
    budget: int = 6  # Task calls the AI thinks it needs
    result: Any = None
    error: str | None = None
    metadata: dict[str, Any] = field(
        default_factory=dict
    )

    LLM_SCHEMA = {
        "id": "short_identifier",
        "description": "what needs to be done",
        "success_criteria": "how to know if it's done",
        "budget": 6
    }

    def start(self):
        self.status = TaskStatus.RUNNING

    def complete(self, result=None):
        self.status = TaskStatus.COMPLETED
        self.result = result

    def fail(self, error: str):
        self.status = TaskStatus.FAILED
        self.error = error

    @staticmethod
    def llm_format():
        return f"""
Return tasks as JSON:

[
  {json.dumps(Task.LLM_SCHEMA, indent=2)}
]

Rules:
- the JSON must be valid
- id must be unique
- description must describe one action
- Do not include status
- Do not include results
- Do not include Markdown Fences or formatting
- 
"""

    def to_dict(self):
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "metadata": self.metadata
        }