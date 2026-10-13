"""The node contract — the contributor door for graph primitives.

A node is a small class with DECLARED PORTS and one method:

    def run(self, state, config) -> None

``state`` is the engine's NodeState for this node (see graph_loop.py):
  * ``state.get(type)``            — read a required input ('goal' is the graph input)
  * ``state.get_optional(type)``   — read an optional input (None if absent)
  * ``state.inputs()``             — (upstream node id, its outputs) in declared order
  * ``state.out(type, value)``     — record a typed output on the blackboard
  * ``state.agent`` / ``state.goal`` / ``state.conversation``
  * ``state.components``           — the per-turn component snapshot (slots)
  * ``state.ctx``                  — the ComponentContext (llm/tools/config/extras)

The v0 type set is CLOSED: context, goal, plan, result, verdict, merged, pass.
``goal`` needs no incoming edge — it is the resolved Goal of the turn.
``side_effects = True`` marks nodes that mutate the world/conversation;
they are rejected at build time inside ``parallel`` branches.

A node is a file + a registration — discovered through one door:
    registry.register_primitive("my_node", MyNode())
#tags graph-loop primitives contract contributor
"""
from abc import abstractmethod
from typing import ClassVar


class NodeInputError(RuntimeError):
    """A required input is missing (no upstream node provided it, or the
    upstream node failed and was skipped)."""


class NodeConfigError(RuntimeError):
    """The node's configuration or its environment (slots/LLM) is incomplete."""


def describe_error(exc: BaseException) -> str:
    """A non-empty one-line description of ``exc``.

    The type name is ALWAYS present; the message follows when it is non-empty
    (e.g. ``"OllamaResponseError: model not loaded (status 500)"``). Bare
    exceptions such as ``asyncio.CancelledError`` have an empty ``str`` —
    using only ``str(exc)`` would surface as a 500 with an empty detail,
    so the type name is the mandatory piece.
    """
    msg = str(exc).strip()
    return f"{type(exc).__name__}" + (f": {msg}" if msg else "")


class NodeBase:
    """Base class for graph nodes — declare ports, implement ``run``."""

    name: ClassVar[str] = "node"
    version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = ""

    # Ports must stay consistent with schemas.graph.PRIMITIVES (the closed
    # v0 vocabulary). The schemas module is the source of truth for
    # validation; these tuples document the same shape at the class level.
    provides: ClassVar[tuple] = ()
    accepts: ClassVar[tuple] = ()
    requires: ClassVar[tuple] = ()
    requires_any: ClassVar[tuple] = ()
    side_effects: ClassVar[bool] = False

    @abstractmethod
    def run(self, state, config):
        """Read ``requires`` from ``state``, do the work, record ``provides``.

        ``config`` is the node's free-form graph config (a dict). Raising is
        always safe: the engine applies the node's ``on_failure`` policy
        (retry / skip / abort) and emits the failure event.
        """
        raise NotImplementedError
