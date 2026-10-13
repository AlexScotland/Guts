"""prompt — inject a fixed prompt into the turn (no slot, no LLM).

Contract (v0):
  requires:  (none — 'goal' optional, no incoming edge needed)
  provides:  'context' (the injected text)
  config:    {'text': str} — required
  side effects: none beyond one serial conversation commit (top level only)
"""
from langchain_core.messages import HumanMessage

from .base import NodeBase, NodeConfigError


class PromptNode(NodeBase):
    name = "prompt"
    provides = ("context",)
    accepts = ("goal",)
    description = "Injects a fixed prompt as 'context' (config.text)"

    def run(self, state, config):
        text = (config or {}).get("text")
        if not text or not isinstance(text, str):
            raise NodeConfigError("prompt node requires config: {'text': '<prompt>'}")
        state.conversation.add(HumanMessage(content=text))
        state.out("context", text)
