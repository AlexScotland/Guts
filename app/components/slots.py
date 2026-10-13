"""The named slots of the agent loop.

Every role the loop needs is a *slot*: a stable name with a stable contract.
Implementations are registered against a slot under an *alias* and activated
at runtime, so any slot can be swapped without touching the loop or the
composition code.

The six slots:

  goal_resolver - turns user input into a Goal (LLM extraction, passthrough, ...)
  planner       - turns a Goal into tasks (LLM planning, trivial single-task, ...)
  executor      - runs one task (LLM+tools, echo, ...)
  evaluator     - decides continue/complete/replan after each task
  memory        - the conversation store; created PER AGENT (each AgentRuntime
                  gets its own store, so server requests stay concurrency-safe)
  loop          - the loop topology itself: which roles run, in what order,
                  and when the turn ends (plan-execute-evaluate vs direct, ...)
"""
from enum import Enum


class ComponentSlot(str, Enum):
    GOAL_RESOLVER = "goal_resolver"
    PLANNER = "planner"
    EXECUTOR = "executor"
    EVALUATOR = "evaluator"
    MEMORY = "memory"
    LOOP = "loop"


ALL_SLOTS = list(ComponentSlot)
