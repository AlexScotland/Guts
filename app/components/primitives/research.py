"""research — context gathering through the active executor (read-only by contract).

Contract (v0):
  requires:  'goal' (graph input), optional 'context'
  provides:  'context' and 'result' (the gathered text)
  maps to:   the active ``executor`` slot — READ-ONLY by contract: its task
             text forbids mutating tools; the slot decides the mechanism
  budget:    consumes executor-budget units (node budget caps the task budget)
"""
from tasks.task import Task

from .base import NodeBase, NodeConfigError


class ResearchNode(NodeBase):
    name = "research"
    provides = ("context", "result")
    accepts = ("goal", "context")
    requires = ("goal",)
    description = "Read-only context gathering via the active executor slot"

    def run(self, state, config):
        executor = state.components.executor if state.components is not None else None
        if executor is None:
            raise NodeConfigError("research node needs the 'executor' slot active")
        goal = state.goal
        if goal is None:
            raise NodeConfigError("research node needs the resolved goal (graph input 'goal')")
        goal_text = getattr(goal, "text", None) or str(goal)
        description = (config or {}).get("task") or (
            f"Read-only research: gather context for the goal below. "
            f"Do NOT modify files, memory, or anything outside this task.\n\n{goal_text}"
        )
        prior = state.get_optional("context")
        if prior:
            description += f"\n\nPrior context:\n{prior}"
        task = Task(
            id=state.node_id,
            description=description,
            success_criteria="context gathered",
            budget=state.node_budget,
        )
        result = executor.execute(task, state.agent)
        if result is None:
            text = ""
        elif isinstance(result, str):
            text = result
        else:
            text = str(getattr(result, "content", result))
        text = text.strip()
        state.out("result", text)
        state.out("context", text)
