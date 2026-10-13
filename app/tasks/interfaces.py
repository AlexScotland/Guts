from abc import ABC, abstractmethod

from tasks.task import Task
from state import AgentState


class TaskExecutor(ABC):

    @abstractmethod
    def execute(
        self,
        task: Task,
        state: AgentState
    ):
        pass
