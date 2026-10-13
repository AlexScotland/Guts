from dataclasses import dataclass
from enum import Enum

from tasks.task import Task, TaskStatus


class EvaluationAction(str, Enum):
    CONTINUE = "continue"
    COMPLETE = "complete"
    REPLAN = "replan"


@dataclass
class EvaluationResult:
    action: EvaluationAction
    reason: str


@dataclass
class AlwaysCompleteEvaluator:
    """Evaluates every task as COMPLETE.

    A deliberate hot-swap target: pair with DirectLoop / EchoExecutor (the
    `offline` preset) for a loop that always answers on the first pass, or
    use in tests to pin down loop behavior without criteria matching.
    """

    def evaluate(self, state, task) -> EvaluationResult:
        return EvaluationResult(
            action=EvaluationAction.COMPLETE,
            reason="unconditionally complete",
        )


@dataclass
class TaskEvaluator:
    """
    Evaluates the results of a task execution to determine if the goal has been achieved.
    """

    def check_success_criteria(self, task) -> EvaluationResult:
        """
        Checks if the task result meets the success criteria.

        Args:
            task: The task object containing the result and success criteria.
        """
        if task.status != TaskStatus.COMPLETED:
            return EvaluationResult(
                action=EvaluationAction.CONTINUE,
                reason="Task has not completed"
            )
        
        result = str(task.result).lower()
        criteria = str(task.success_criteria).lower()

        if criteria in result:
            return EvaluationResult(
                action=EvaluationAction.COMPLETE,
                reason="Task returned a result meeting success criteria"
            )

        return EvaluationResult(
            action=EvaluationAction.CONTINUE,
            reason="Task completed but criteria not met"
        )

    def evaluate(self, state: dict, task: Task) -> EvaluationResult:
        """
        Evaluates the task result and determines if the goal is complete.

        Args:
            state (dict): The current state of the agent.
            task (Task): The task object containing the result and success criteria.
        """
        if task.result is None:
            return EvaluationResult(
                action=EvaluationAction.CONTINUE,
                reason="No result produced"
            )

        if task.success_criteria:
            return self.check_success_criteria(
                task
            )

        return EvaluationResult(
            action=EvaluationAction.CONTINUE,
            reason="No completion criteria"
        )
