"""The agent loop, segregated into swappable Loop policies.

A Loop owns the topology of a turn: which roles run, in what order, and when
the turn ends. Roles arrive as a per-turn snapshot in ``LoopComponents``;
the loop never touches the registry itself, so loops are independently testable
and composable.

Ship with two:
  PlanExecuteLoop - plan -> execute -> evaluate, with REPLAN support
                    (the classic loop, behavior preserved from AgentRuntime).
  DirectLoop      - one synthesized task straight through the executor;
                    no planning step, no replan.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from state import AgentStatus
from tasks.task import Task

from evaluator import EvaluationAction


@dataclass
class LoopComponents:
    """Per-turn snapshot of the roles a loop may use.

    Entries may be ``None`` (e.g. no goal_resolver registered); a loop
    must degrade gracefully for the None slots it tolerates.
    """

    goal_resolver: Any = None
    planner: Any = None
    executor: Any = None
    evaluator: Any = None
    loop: Any = None  # the loop instance itself, snapshotted with the turn

    def require(self, name: str) -> Any:
        value = getattr(self, name)
        if value is None:
            raise RuntimeError(f"Loop requires the {name!r} component, but none is active")
        return value


class AgentLoop(ABC):
    """Contract for a turn: consume state + component snapshot, return the answer."""

    name: str = "loop"
    version: str = "1.0.0"

    @abstractmethod
    def run(self, components: LoopComponents, state) -> Any:
        """Execute one turn against ``state`` and return the final answer."""


class PlanExecuteLoop(AgentLoop):
    """The classic plan -> execute -> evaluate loop with replan support.

    Preserves the exact behavior of the old AgentRuntime._execute():
      - the goal banner is appended to the conversation,
      - the planner produces the task list,
      - each task runs, then the evaluator decides,
      - CONTINUE advances to the next task, REPLAN re-plans from the current
        task index, COMPLETE ends the turn immediately,
      - a turn that exhausts its plan ends COMPLETED with the last result.
    """

    name = "plan-execute"
    version = "1.0.0"

    def run(self, components: LoopComponents, state) -> Any:
        planner = components.require("planner")
        executor = components.require("executor")
        evaluator = components.require("evaluator")

        state.add_goal()
        state.transition_to(AgentStatus.PLANNING)
        tasks = planner.create_plan(state)
        state.set_plan(tasks)
        state.transition_to(AgentStatus.EXECUTING)

        last_result = None
        while (task := state.get_current_task()) is not None:
            last_result = executor.execute(task, state)

            evaluation = evaluator.evaluate(state, task)

            if evaluation.action == EvaluationAction.COMPLETE:
                return last_result

            if evaluation.action == EvaluationAction.REPLAN:
                tasks = planner.create_plan(state)
                state.set_plan(tasks)
                continue

            state.complete_current_task()

        state.transition_to(AgentStatus.COMPLETED)
        return last_result


class DirectLoop(AgentLoop):
    """Single-pass loop: goal straight through the executor.

    No planner, no replan: the goal text becomes one synthesized task, the
    executor runs it, and whatever the evaluator decides is accepted as the
    final answer (a DirectLoop has no plan to return a replan to).
    """

    name = "direct"
    version = "1.0.0"

    def run(self, components: LoopComponents, state) -> Any:
        executor = components.require("executor")

        state.add_goal()
        state.transition_to(AgentStatus.EXECUTING)

        goal_text = getattr(state.goal, "text", None) or str(state.goal)
        task = Task(id="direct", description=goal_text, success_criteria="goal satisfied")
        state.set_plan([task])

        result = executor.execute(task, state)

        if components.evaluator is not None:
            # Consult the evaluator for parity (logging/observability), but
            # DirectLoop has no second pass to hand a replan back to.
            components.evaluator.evaluate(state, task)

        state.transition_to(AgentStatus.COMPLETED)
        return result
