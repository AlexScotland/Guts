"""parallel — bounded fan-out over inline branch graphs (concurrent, isolated).

Contract (v0):
  requires:  'goal' (inherited by every branch), optional 'context'
  provides:  'merged' — list of branch answers in DECLARED branch order
             (a branch that failed at run-time is a NULL slot)
  rules:     branches run concurrently (asyncio/thread pool, max_parallel cap);
             each branch gets a FRESH blackboard (fresh conversation store);
             side-effect nodes (act) are rejected inside branches at build time;
             only a declared 'merge' node afterwards commits branch output to the
             shared conversation, in declared order
  failure:   a branch's RUNTIME failure (executor/LLM error, a leaked
             CancelledError) degrades THAT branch to a null slot — siblings
             finish, the turn completes, and the engine emits one visible
             `branch_failed` event per degraded branch; contract violations
             (a binding budget ceiling, an incomplete node environment)
             still fail the WHOLE RUN (fail-fast is the default)
"""
from .base import NodeBase, NodeConfigError


class ParallelNode(NodeBase):
    name = "parallel"
    provides = ("merged",)
    accepts = ("goal", "context")
    requires = ("goal",)
    description = "Runs inline branch graphs concurrently (fresh blackboard per branch)"

    def run(self, state, config):
        branches = state.branches
        if not branches:
            raise NodeConfigError("parallel node requires a non-empty 'branches' list")
        context = state.get_optional("context")
        results = state.run_branches(branches, state.max_parallel, context)
        state.out("merged", results)
