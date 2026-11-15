"""``design`` — the stateless design face for GUTS graph loops.

The user's one move (anywhere: HTTP endpoint, MCP tool, chat panel) is a
sentence: a goal, optional business-case notes, and optionally the current
document to re-draft. This package turns that into a proposed graph
document + a verdict.

Deliberately stateless and loop-blind (docs/graph-loop-designer.md,
"AI-assisted design"):

  * ``draft(goal, notes, current)`` has ZERO loop-count intelligence —
    "loop twice" vs "loop until it feels done" is business-case material
    that rides in the model's choice of ``max_passes``/node config
    according to the notes. No rule here counts loops or knows what a
    loop is for;
  * the ONLY green door is the backend ``validate_graph`` (schemas/graph.py)
    — the verdict this package reports is exactly that result, and a red
    verdict is a normal outcome (the caller owns any refinement);
  * ``catalog`` exposes the frozen graph language (``list_primitives`` /
    ``ports_of`` / ``wire_check``) as pure functions over the same
    ``PRIMITIVES`` table the backend validates against — no drift.
"""
