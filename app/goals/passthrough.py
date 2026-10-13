"""LLM-free goal resolution: wrap the raw input in a minimal Goal.

Use as a hot-swap target when the LLM is unavailable or you want zero
extraction latency (the `offline` preset does exactly this). Keeps the rest
of the harness happy because it still returns a Goal, just with the intent
being the input text itself.
"""
from .goal import Goal


class PassthroughGoalResolver:
    """No LLM call; the user's message becomes the goal intent verbatim."""

    def extract(self, user_input, history=None) -> Goal:
        return Goal(intent=str(user_input).strip())
