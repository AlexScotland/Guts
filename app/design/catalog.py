"""The frozen graph language as data — pure functions over ``PRIMITIVES``.

Built on the same table the backend validates against (schemas/graph.py),
so what the design model is told and what ``validate_graph`` enforces can
not drift. All functions are PURE: no I/O, no LLM, no imports beyond the
schema module.
"""
from schemas.graph import GRAPH_INPUTS, KNOWN_PRIMITIVES, PRIMITIVES

#: One-line, human-readable role per primitive (the v0 vocabulary comment in
#: schemas/graph.py is the source; kept short so it fits in a prompt).
PRIMITIVE_NOTES = {
    "prompt": "Inject a fixed prompt text (config.text).",
    "research": "Read-only, tool-backed context gathering.",
    "plan": "Produce a task list (the active planner slot).",
    "act": "Performs one step; mutates world/conversation. Never inside a parallel branch.",
    "critic": "Judges a result into a verdict (pass = stop the loop).",
    "gate": "Deterministic verdict -> pass projection.",
    "merge": "Commits upstream outputs to the conversation, in declared order.",
    "parallel": "Fans the goal out over 1+ inline branch graphs (fresh blackboard each); no act inside branches.",
    "route": "(v1) Dynamic choice among its outgoing data edges.",
}

#: The frozen contract rules, stated for the design model and exposed in the
#: catalog. Mirrors the docstring contract of schemas/graph.py.
RULES = [
    "goal is the graph input: the entry node needs no edge for it, and goal NEVER rides an edge.",
    "Exactly one entry node: zero incoming data edges, feedable from the goal alone; every other node reachable from it over data edges.",
    "Data edges are acyclic: a data edge source->target is legal only if the source provides something the target accepts.",
    "Loops exist only as declared control edges: each must carry max_passes >= 1, and a control edge may only LEAVE a critic or gate node.",
    "parallel takes 1+ inline branch graphs (each a full mini document with its own nodes/edges); each branch has exactly one entry and no act node.",
    "A data edge into a parallel node carries the goal; it provides 'merged' in declared branch order (a failed branch is a null slot).",
    "Budgeted primitives (research, act, route) may carry a per-node 'budget' (int, executor-budget units); an optional graph 'budget' is the binding ceiling.",
    "Node ids are short, readable: [A-Za-z0-9][A-Za-z0-9_-]*. Node config keys are primitive-specific (e.g. research uses 'task', prompt uses 'text').",
    "Optional per-node 'on_failure': 'abort' (default) | 'skip' | an object with 'retry'.",
    "Document shape: {name?, description?, budget?, max_parallel?, nodes: {id: {primitive, config?, budget?, on_failure?}}, edges: [{from, to, type: data|control, max_passes?}]}.",
]


def list_primitives() -> list[dict]:
    """The closed vocabulary as plain dicts (prompt + catalog surface)."""
    out = []
    for name in KNOWN_PRIMITIVES:
        meta = PRIMITIVES[name]
        out.append({
            "primitive": name,
            "description": PRIMITIVE_NOTES.get(name, ""),
            "requires": list(meta.requires),
            "requires_any": [list(g) for g in meta.requires_any],
            "accepts": list(meta.accepts),
            "provides": list(meta.provides),
            "side_effects": meta.side_effects,
            "budgeted": meta.budgeted,
        })
    return out


def ports_of(primitive: str) -> dict:
    """One primitive's port shape. Raises ValueError for an unknown name."""
    if primitive not in PRIMITIVES:
        raise ValueError(
            f"unknown primitive {primitive!r} — the closed set is: "
            + ", ".join(KNOWN_PRIMITIVES)
        )
    meta = PRIMITIVES[primitive]
    return {
        "primitive": primitive,
        "requires": list(meta.requires),
        "requires_any": [list(g) for g in meta.requires_any],
        "accepts": list(meta.accepts),
        "provides": list(meta.provides),
        "side_effects": meta.side_effects,
        "budgeted": meta.budgeted,
    }


def wire_check(source: str, target: str) -> bool:
    """Can a DATA edge carry from ``source`` to ``target``?

    Exactly the backend rule (edgeTypable in the designer, _validate in
    schemas/graph.py): the source provides at least one type the target
    accepts, with ``goal`` excluded (the goal is the graph input and never
    rides an edge). Unknown primitives are simply not wireable.
    """
    try:
        src = PRIMITIVES[source]
        dst = PRIMITIVES[target]
    except KeyError:
        return False
    return bool(set(src.provides) & (set(dst.accepts) - set(GRAPH_INPUTS)))
