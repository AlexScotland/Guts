"""The 8 v0 graph primitives — one file per node, all discovered through the
registry's ``primitives`` namespace (one door: ``describe()`` / GET /api/components).

Contributor story: add a node = one subclass of NodeBase implementing
``run(state, config)``, plus one entry in NODE_CLASSES (or register it with
``registry.register_primitive``). It appears in ``GET /api/components``, is
selectable in the designer, hot-swaps like everything else, and tests write
against the same fakes as the core.
"""
from .act import ActNode
from .base import NodeBase, NodeConfigError, NodeInputError
from .critic import CriticNode, Verdict
from .gate import GateNode
from .merge import MergeNode
from .parallel import ParallelNode
from .plan import PlanNode
from .prompt import PromptNode
from .research import ResearchNode
from .route import Choice, RouteNode

#: name -> class, in the order of the closed vocabulary (the 8 v0
#: primitives + the v1 router, docs/ai-routing-nodes.md).
NODE_CLASSES = {
    cls.name: cls
    for cls in (
        PromptNode,
        ResearchNode,
        PlanNode,
        ActNode,
        CriticNode,
        GateNode,
        MergeNode,
        ParallelNode,
        RouteNode,
    )
}

__all__ = [
    "ActNode",
    "Choice",
    "CriticNode",
    "GateNode",
    "MergeNode",
    "NODE_CLASSES",
    "NodeBase",
    "NodeConfigError",
    "NodeInputError",
    "ParallelNode",
    "PlanNode",
    "PromptNode",
    "ResearchNode",
    "RouteNode",
    "Verdict",
]
