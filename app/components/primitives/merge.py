"""merge — the ONLY committer of branch/parallel outputs to the conversation.

Contract (v0):
  requires:  (no typed require — it fans in whatever its declared input edges carry)
  provides:  'merged' (list, declared order), 'result' (joined text), 'context'
  rule:      commits its inputs to state.conversation IN DECLARED EDGE ORDER —
             this is what makes parallel fan-out deterministic (the engine runs
             branches concurrently, but their commits land here, serially, in order);
             NULL slots (a parallel branch that failed at run-time) are simply
             not committed — the turn degrades to what succeeded
"""
from langchain_core.messages import AIMessage

from .base import NodeBase


class MergeNode(NodeBase):
    name = "merge"
    provides = ("merged", "result", "context")
    accepts = ("result", "merged")
    description = "Commits upstream outputs to the conversation in declared order"

    def run(self, state, config):
        config = config or {}
        parts: list[str] = []
        for node_id, outputs in state.inputs():
            value = outputs.get("result")
            if value is None:
                value = outputs.get("merged")
            if value is None:
                continue
            # A parallel's 'merged' is a LIST of branch answers in declared
            # order. Flatten it — committing str(['a','b']) would leak a
            # Python repr into the conversation instead of clean joined text.
            items = list(value) if isinstance(value, (list, tuple)) else [value]
            prefix = f"[{node_id}] " if config.get("labeled") else ""
            for item in items:
                if item is None:
                    continue
                state.conversation.add(AIMessage(content=prefix + str(item)))
                parts.append(str(item))
        joined = "\n\n".join(parts)
        state.out("merged", parts)
        state.out("result", joined)
        state.out("context", joined)
