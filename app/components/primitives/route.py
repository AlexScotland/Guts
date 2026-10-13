"""route — dynamic choice among the node's wired branches (v1).

Contract (docs/ai-routing-nodes.md):
  accepts:   'goal' | 'context' | 'result' (whichever upstream data flows in)
  provides:  the incoming payload passed through as BOTH 'context' and
             'result' (the research-style duality) + '{choice, reason}' —
             reason is MANDATORY: the trust cost of a non-deterministic hop
             is paid in the output, not in the docs
  deciders:  'manual' — config.pinned (zero LLM calls; the deterministic
             test/debug decider, or an offline graph with budget 0)
             'llm'    — one structured single-choice call (the critic's
             LLM-with-schema pattern; fakes pin it in tests)
             'rule'   — reserved (unimplemented); the validator rejects it
  fallback:  an invalid llm choice is NOT silently retried — the declared
             'fallback' edge is taken (visible in the outputs), else the
             node's on_failure policy applies (one rule, no retry magic)
  budget:    the decision costs exactly one executor-budget unit; a node
             budget of 0 is offline mode by construction (the llm decider
             takes the fallback without an LLM call)
  side effects: none — the choice itself is inert (allowed inside parallel
             branches, like the validator already admits)
"""
from langchain_core.messages import SystemMessage, HumanMessage
from pydantic import BaseModel, Field

from .base import NodeBase, NodeConfigError


class Choice(BaseModel):
    """The LLM decider's structured single choice (kept keyword-safe)."""

    choice: str = Field(description="The id of EXACTLY ONE of the offered branches.")
    reason: str = Field(description="Mandatory: one or two sentences on why this branch.")


class RouteNode(NodeBase):
    name = "route"
    accepts = ("goal", "context", "result")
    provides = ("context", "result")
    side_effects = False
    description = "Chooses exactly one outgoing branch (manual/llm decider); payload passes through"

    # ── the incoming payload (what flows in is what passes on) ────
    def _payload(self, state):
        for t in ("context", "result", "goal"):
            value = state.get_optional(t)
            if value is not None:
                return value
        return None

    # ── the llm decider (structured single choice, mandatory reason) ──
    def _pick_llm(self, state, config, options, payload) -> tuple[str, str]:
        llm = state.ctx.llm if state.ctx is not None else None
        if llm is None or not hasattr(llm, "with_structured_output"):
            raise NodeConfigError(
                "route node needs an LLM (context.llm) for the 'llm' decider"
            )
        goal = state.goal
        goal_text = (getattr(goal, "text", None) or str(goal)) if goal is not None else "(no goal)"
        # Truncated to the EVENT caps — the same head+tail rule the result
        # events use, shared from the engine (one definition of the caps).
        # Lazy import: graph_loop imports this package — no module cycle.
        from ..graph_loop import _string_view

        payload_text = _string_view(str(payload))[0] if payload is not None else "(none)"
        option_lines = "\n".join(f"  - {branch_id}: {desc}" for branch_id, desc in options.items())
        messages = [
            SystemMessage(
                content=(
                    "You are a routing dispatcher for an agent harness. Choose EXACTLY ONE of "
                    "the offered branches for the goal below. Reply with the structured choice "
                    "only; the reason is mandatory."
                )
            ),
            HumanMessage(
                content=(
                    f"GOAL:\n{goal_text}\n\n"
                    f"INPUT PAYLOAD:\n{payload_text}\n\n"
                    f"BRANCHES:\n{option_lines}\n\n"
                    "Choose exactly one branch id and explain the choice in the reason."
                )
            ),
        ]
        verdict = llm.with_structured_output(Choice).invoke(messages)
        choice = str(getattr(verdict, "choice", "")).strip()
        reason = str(getattr(verdict, "reason", "")).strip()
        return choice, reason

    def run(self, state, config):
        config = config or {}
        options = config.get("options") or {}
        if not isinstance(options, dict) or not options:
            raise NodeConfigError(
                "route node needs config.options (id -> description)"
            )
        decider = config.get("decider")
        fallback = config.get("fallback")
        payload = self._payload(state)

        if decider == "manual":
            # Zero LLM calls: the deterministic decider (test/debug mode, or a
            # fully offline graph — it needs no budget).
            choice = str(config.get("pinned") or "").strip()
            if choice not in options:
                raise NodeConfigError(
                    f"route node (manual decider) has no valid config.pinned "
                    f"(choice set: {sorted(options)})"
                )
            reason = f"manual decision: pinned branch {choice!r}"
        elif decider == "llm":
            if state.node_budget == 0:
                # Offline mode by construction: no LLM call, the declared
                # fallback edge is taken (or the node fails visibly).
                if fallback not in options:
                    raise NodeConfigError(
                        "route node budget is 0 (offline) and no 'fallback' branch is declared"
                    )
                choice = fallback
                reason = "budget 0 (offline mode): decision skipped, taking the fallback branch"
            else:
                choice, reason = self._pick_llm(state, config, options, payload)
                if choice not in options:
                    if fallback in options:
                        reason = (
                            f"invalid choice {choice!r}; taking the fallback branch"
                            + (f" (llm: {reason})" if reason else "")
                        )
                        choice = fallback
                    else:
                        raise NodeConfigError(
                            f"route node LLM named unknown branch {choice!r} "
                            f"and no 'fallback' branch is declared"
                        )
            if not reason:
                raise NodeConfigError("route node LLM returned no reason (mandatory)")
        else:
            raise NodeConfigError(
                f"route node has no valid config.decider (got {decider!r}); "
                f"the validator should have caught this"
            )

        state.out("choice", choice)
        state.out("reason", reason)
        passed = payload if payload is not None else ""
        state.out("context", passed)
        state.out("result", passed)
