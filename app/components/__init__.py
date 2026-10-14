"""Hot-swappable segregation of the agent loop.

The harness is composed of six named slots (goal_resolver, planner,
executor, evaluator, memory, loop). Each slot holds aliased implementations;
a ComponentRegistry activates one alias per slot at runtime, per-turn
snapshots keep an in-flight turn stable, and JSON manifests / named presets
let a whole configuration be swapped in one call.

    from components import (
        ComponentContext, ComponentRegistry, ComponentSlot,
        register_builtin_components, apply_manifest,
    )
"""
from .agent_bind import (
    AgentBindError,
    bind_agent,
    ensure_agent_bound,
    get_mcp_pool,
    reset_mcp_pool,
)
from .builtin import (
    COMPONENT_ALIASES,
    PRESETS,
    register_agent_components,
    register_builtin_components,
    register_graph_components,
)
from .context import ComponentContext
from .graph_loop import GraphLoop
from .loops import AgentLoop, DirectLoop, LoopComponents, PlanExecuteLoop
from .manifest import ManifestError, apply_manifest, load_manifest
from .registry import (
    ComponentAliasError,
    ComponentBuildError,
    ComponentNotRegisteredError,
    ComponentRegistry,
    PER_AGENT_SLOTS,
    StaticRegistry,
)
from .slots import ALL_SLOTS, ComponentSlot

__all__ = [
    "AgentBindError",
    "AgentLoop",
    "ALL_SLOTS",
    "ComponentAliasError",
    "ComponentBuildError",
    "ComponentContext",
    "ComponentNotRegisteredError",
    "ComponentRegistry",
    "ComponentSlot",
    "DirectLoop",
    "GraphLoop",
    "LoopComponents",
    "ManifestError",
    "PER_AGENT_SLOTS",
    "PlanExecuteLoop",
    "StaticRegistry",
    "apply_manifest",
    "bind_agent",
    "ensure_agent_bound",
    "get_mcp_pool",
    "load_manifest",
    "reset_mcp_pool",
    "register_agent_components",
    "register_builtin_components",
    "register_graph_components",
]
