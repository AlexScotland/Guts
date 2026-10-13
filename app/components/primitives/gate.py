"""gate — deterministic verdict -> pass projection (no LLM).

Contract (v0):
  requires:  'verdict'
  provides:  'pass' (bool) — read from the verdict dict/object
  maps to:   nothing (pure function of the blackboard)
"""
from .base import NodeBase


class GateNode(NodeBase):
    name = "gate"
    provides = ("pass",)
    accepts = ("verdict",)
    requires = ("verdict",)
    description = "Projects a verdict onto a boolean 'pass' (no LLM)"

    def run(self, state, config):
        verdict = state.get("verdict")
        if isinstance(verdict, dict):
            passed = verdict.get("pass")
        else:
            passed = getattr(verdict, "pass", None)
        if passed is None:
            decision = getattr(verdict, "decision", None)
            passed = str(decision).lower() == "pass" if decision is not None else None
        if passed is None:
            from .base import NodeInputError

            raise NodeInputError(f"gate node: verdict {verdict!r} has no usable 'pass'")
        state.out("pass", bool(passed))
