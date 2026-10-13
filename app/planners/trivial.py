"""LLM-free planner: always a single task describing the whole goal.

Use as a hot-swap target to skip the planning round-trip (the `fast` preset
pairs it with DirectLoop for a minimal two-call turn: goal + execute).
"""
from planners.interfaces import Planner
from tasks.task import Task


class TrivialPlanner(Planner):
    """One task, no LLM, deterministic."""

    def create_plan(self, state) -> list[Task]:
        goal_text = getattr(state.goal, "text", None) or str(state.goal)
        return [
            Task(
                id="t1",
                description=goal_text,
                success_criteria="goal satisfied",
                budget=6,
            )
        ]
