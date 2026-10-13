"""critic — judge a result: active evaluator first, then LLM-with-schema.

Contract (v0):
  requires:  'result', optional 'goal'
  provides:  'verdict' ({pass, reason}) and 'pass' (bool)
  maps to:   the active ``evaluator`` slot as a cheap heuristic; when it is
             inconclusive (CONTINUE), one structured-LLM judgment call
             (the frozen v0 leaning: LLM with a schema — fakes in tests)
"""
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from evaluator import EvaluationAction

from .base import NodeBase, NodeConfigError


class Verdict(BaseModel):
    """The LLM judgment schema (kept keyword-safe: no field named ``pass``)."""

    decision: str = Field(description="'pass' or 'fail' — does the result satisfy the goal?")
    feedback: str = Field(default="", description="One or two sentences: what is missing (if fail).")


class CriticNode(NodeBase):
    name = "critic"
    provides = ("verdict", "pass")
    accepts = ("result", "goal")
    requires = ("result",)
    description = "Evaluator heuristic first, then LLM verdict (decision/feedback)"

    # ── heuristic pass (the active evaluator slot) ──
    def _heuristic(self, state):
        evaluator = state.components.evaluator if state.components is not None else None
        if evaluator is None:
            return None
        plan = list(getattr(state.agent, "plan", None) or [])
        task = None
        for candidate in reversed(plan):
            status = candidate.status
            if hasattr(status, "value"):
                status = str(status)
            if status == "completed" and candidate is not None:
                task = candidate
                break
        if task is None:
            return None
        try:
            evaluation = evaluator.evaluate(state.agent, task)
        except Exception:
            return None
        if evaluation.action == EvaluationAction.COMPLETE:
            return (True, evaluation.reason)
        if evaluation.action == EvaluationAction.REPLAN:
            return (False, evaluation.reason)
        return None  # CONTINUE: inconclusive → LLM judgment

    # ── LLM judgment (structured, schema-fixed) ──
    def _llm_verdict(self, state, result) -> tuple[bool, str]:
        llm = state.ctx.llm if state.ctx is not None else None
        if llm is None or not hasattr(llm, "with_structured_output"):
            raise NodeConfigError(
                "critic node needs an LLM (context.llm) for judgment, or an evaluator "
                "slot that reaches a definitive decision"
            )
        goal = state.goal
        goal_text = getattr(goal, "text", None) or str(goal) if goal is not None else "(no goal)"
        messages = [
            SystemMessage(
                content=(
                    "You are a strict critic for an agent harness. Judge whether the "
                    "result satisfies the goal. Reply with the structured verdict only."
                )
            ),
            HumanMessage(content=f"GOAL:\n{goal_text}\n\nRESULT:\n{result}"),
        ]
        verdict = llm.with_structured_output(Verdict).invoke(messages)
        decision = str(getattr(verdict, "decision", "")).strip().lower()
        feedback = str(getattr(verdict, "feedback", ""))
        if decision not in ("pass", "fail"):
            raise NodeConfigError(f"critic LLM returned non-judgment {decision!r}")
        return (decision == "pass", feedback)

    def run(self, state, config):
        result = state.get("result")
        judged = self._heuristic(state)
        if judged is None:
            judged = self._llm_verdict(state, result)
        passed, reason = judged
        state.out("verdict", {"pass": passed, "reason": reason})
        state.out("pass", passed)
