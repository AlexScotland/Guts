"""plan — produce the task list through the active planner slot.

Contract (v0):
  requires:  'goal' (graph input), optional 'context'
  provides:  'plan' (a list of tasks)
  maps to:   the active ``planner`` slot (planner.default / trivial / custom)
"""
from tasks.task import Task

from .base import NodeBase, NodeConfigError


class PlanNode(NodeBase):
    name = "plan"
    provides = ("plan",)
    accepts = ("goal", "context")
    requires = ("goal",)
    description = "Task planning via the active planner slot"

    def run(self, state, config):
        planner = state.components.planner if state.components is not None else None
        if planner is None:
            raise NodeConfigError("plan node needs the 'planner' slot active")
        tasks = planner.create_plan(state.agent)
        normalized = [
            t if isinstance(t, Task)
            else Task(id=f"{state.node_id}-{i}", description=str(t), budget=state.node_budget)
            for i, t in enumerate(tasks or [])
        ]
        if not normalized:
            raise NodeConfigError("plan node produced an empty task list")
        # Visibility for evaluators/loops; the graph engine drives order itself.
        state.agent.set_plan(normalized)
        state.out("plan", normalized)
