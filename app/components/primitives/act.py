"""act — do the work through the active executor slot (the only side-effecter).

Contract (v0):
  requires:  'plan' OR 'context' (disjunction — either one suffices)
  provides:  'result' (the executor's answer)
  maps to:   the active ``executor`` slot; each planned task (or one synthesized
             task from the context) runs through ``executor.execute``
  side effects: YES — rejected at build time inside parallel branches
  budget:    consumes executor-budget units (node budget caps each task budget)
"""
import dataclasses

from tasks.task import Task

from .base import NodeBase, NodeConfigError


class ActNode(NodeBase):
    name = "act"
    provides = ("result",)
    accepts = ("plan", "context", "goal")
    requires_any = (("plan", "context"),)
    side_effects = True
    description = "Executes the plan (or context) via the active executor slot"

    def run(self, state, config):
        executor = state.components.executor if state.components is not None else None
        if executor is None:
            raise NodeConfigError("act node needs the 'executor' slot active")
        config = config or {}

        plan = state.get_optional("plan")
        if plan:
            tasks = list(plan)
        else:
            context = state.get_optional("context")
            if context is None:
                from .base import NodeInputError

                raise NodeInputError("act node needs 'plan' or 'context'")
            goal = state.goal
            goal_text = getattr(goal, "text", None) or str(goal)
            description = config.get("task") or (
                f"Complete the goal using the provided context.\n\nGoal:\n{goal_text}\n\n"
                f"Context:\n{context}"
            )
            tasks = [
                Task(
                    id=state.node_id,
                    description=description,
                    success_criteria=config.get("success_criteria", "goal satisfied"),
                    budget=state.node_budget,
                )
            ]

        last = None
        for i, task in enumerate(tasks):
            if not isinstance(task, Task):
                task = Task(id=f"{state.node_id}-{i}", description=str(task), budget=state.node_budget)
            # Per-node budget ceiling (binding over the task's own estimate).
            budget = state.node_budget if state.node_budget is not None else task.budget
            if task.budget != budget:
                task = dataclasses.replace(task, budget=budget)
            last = executor.execute(task, state.agent)
            status = task.status
            if hasattr(status, "value"):
                status = str(status)
            if status not in ("completed", "failed") and task.result is not None and last is not None:
                task.complete(last)
        state.out("result", last)
