from dataclasses import dataclass, field
from typing import Any
from enum import Enum

from langchain_core.messages import SystemMessage, HumanMessage

from memory import ConversationMemory



class AgentStatus(str, Enum):
    INITIALIZING = "initializing"
    PLANNING = "planning"
    EXECUTING = "executing"
    REFLECTING = "reflecting"
    COMPLETED = "completed"
    FAILED = "failed"
    WAITING = "waiting"

@dataclass
class AgentState:
    agent_id: str
    conversation: ConversationMemory
    goal: str | None = None
    plan: list = field(default_factory=list)
    current_task: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    status: AgentStatus = AgentStatus.INITIALIZING

    def initialize(self, system_prompt: str):
        self.conversation.add(SystemMessage(content=system_prompt))

    def set_conversation(self, conversation: ConversationMemory):
        """Swap the backing conversation store (memory-slot hot-swap).

        Called by the runtime when the active ``memory`` component changes:
        the new store starts empty, which is the documented effect of
        switching memory backends mid-session.
        """
        self.conversation = conversation

    def add_message(self, message):
        self.conversation.add(message)

    def add_goal(self):
        self.conversation.add(HumanMessage(content=self.goal.text))

    def set_plan(self, plan: list):
        self.plan = plan
        self.current_task = 0

    def transition_to(self, new_status: AgentStatus):
        self.status = new_status

    def get_current_task(self):
        if self.current_task >= len(self.plan):
            return None
        return self.plan[self.current_task]

    def complete_current_task(self):
        self.current_task += 1

    def to_dict(self):
        return {
            "agent_id": self.agent_id,
            "goal": self.goal,
            "plan": self.plan,
            "current_task": self.current_task,
            "metadata": self.metadata,
            "conversation": self.conversation.to_dict()
        }