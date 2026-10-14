# Route node: dynamic choice among wired branches (design)

**Branch:** `ai-routing-nodes` — **Status:** implemented (v1) —
schemas + engine + primitive + tests (backend), designer (choice-set wires,
node panel, skip reasons, took-branch banner), docs clause in
[graph-loop-designer.md](graph-loop-designer.md). Fakes pin the choice —
the `llm` decider stays the declared non-determinism.

## The gap

v0 can fan **out** (`parallel`) and can **retry on failure** (`critic`/`gate` →
control edge, which fires *only when the verdict does not pass* and re-runs a
declared suffix — [graph_loop.py L469-485](../app/components/graph_loop.py)).
What it cannot do is **route by input**: the user's real job is
"if this is a coding question go to the coding flow, otherwise to the research
flow" — and today the only way to express "otherwise" is to hand-write the
condition into the topology, which the v0 vocabulary has no way to carry.
The supervisor pattern (one dispatcher in front of specialist loops) is the
canonical multi-agent shape and it is **not expressible** in the current
contract.

## The feature

One new primitive, `route` — the 9th vocabulary entry (a v1 contract
extension, documented here + tests, per the repo rule that the canvas renders
exactly what the engine can run). It has **no side effects** (the choice
itself is inert) and consumes **one executor-budget unit** for the decision.

**The node is a 3-part configuration** — the parts live on different surfaces
and each fails visibly on its own:

| Part | Surface | Fails |
|---|---|---|
| **Choice set** — which branches may be taken | wiring (outgoing data edges), rendered by the canvas | save time: `config.options` keys must equal the downstream node ids exactly |
| **Decider** — how the choice is made | `config.decider` — `"manual" \| "llm"` (`"rule"` reserved, unimplemented) | save time: closed enum |
| **Decider inputs** — `options` (id → description), `pinned` (manual), `fallback` | `config.*` | save time: `fallback` ∈ choice set; `pinned` ∈ choice set |

Example:

```json
{
  "id": "dispatch",
  "primitive": "route",
  "config": {
    "decider": "llm",
    "options": {
      "agent-web":  "Deep web research, authoritative sources",
      "agent-code": "Writes and tests code, runs files"
    },
    "fallback": "agent-web"
  }
}
```

## Engine semantics

### Active dataflow (the one generalization)

Today *every* reachable node runs. v1 adds: **an edge is active iff its source
node ran; a node runs iff ≥1 incoming edge is active** (the entry node
unconditionally). Graphs without a `route` node behave byte-identically to
today (all sources ran), so v0 semantics — including the pinned
"same graph ⇒ identical conversation order across 5 runs" test — are
preserved. The determinism claim is scoped honestly: *order is deterministic
given the decider's decision; the `llm` decider is the declared
non-determinism* — the same framing as "LLM content is non-deterministic,
order was deterministic." Fakes pin the choice, so existing determinism
tests keep passing.

### Taking exactly one branch

The route node resolves its decider to **one** outgoing edge. That edge's
subtree runs. The other edges are untaken and their downstream subtrees are
**skipped transitively** — any node whose only active upstream path went through
an untaken edge is skipped (emitting the existing `skipped` event with detail
`"not routed"`). No new event kind is invented.

### Convergence (the pinned semantic)

When active and pruned paths re-join, the rule is:

> **A node whose required input type has no active provider (every provider
> upstream is pruned) is `skipped` with reason `"upstream pruned"` — it is not
> a failure.**

Rationale: a pruned branch is a *declared alternative*, not an error; the
pruned subtree should prune cleanly up to the re-join, and `merge` then
commits only what arrived (matching its existing "commit in declared order,
only what's present" behavior). `abort` was considered and rejected: it would
turn normal routing into a failure path. Test suite pins both the skip and
the merge-receives-only-active-outputs cases.

### Failure of the decision itself

An invalid `llm` choice (unknown target — which the validator's choice set
guarantees cannot be wired, but the LLM can still name it) is **not** silently
retried: it is a visible event. If `fallback` is declared, the run takes the
fallback edge; otherwise the node aborts (`on_failure` applies as usual).
One rule, no hidden retry magic.

### Budget + side effects

- Decision = 1 executor-budget unit; the graph ceiling stays binding
  (a budget of 0 for the node ⇒ decision is skipped and `fallback` is taken —
  offline mode by construction).
- `route` has `side_effects = False`, so it is *allowed inside parallel
  branches* (a declared non-determinism inside a branch, documented).

## Deciders

- **`manual`** — `config.pinned` names the target; zero LLM calls. This is
  the deterministic test/debug decider and also how a graph can stay fully
  deterministic while still having the router's topology.
- **`llm`** — one small prompt: goal + the incoming payload (truncated to the
  event caps) + the options table (id → description) + "choose exactly one".
  Structured single-choice output, the same "LLM-with-schema judgment" pattern
  `critic` already uses. Output is always `{choice, reason}` — **reason is
  mandatory** (the trust cost of a non-deterministic hop is paid in the
  output, not in the docs).
- **`rule`** (conditional mapping) — **reserved in the enum, not built**,
  until someone asks for it in writing.

## Port shape (validator entry)

```python
"route": PortShape(
    accepts=("goal", "context", "result"),
    provides=("context", "result"),   # passthrough, research-style duality
    budgeted=True,
)
```

The node passes the incoming payload through to whichever branch runs, as
both `context` and `result` (the `research` node already fixes this
dual-output pattern in the contract).

## Validator additions (schemas/graph.py)

- `route` requires **≥2 outgoing data edges** (one target is a plain edge;
  zero is a dead end) — otherwise `GraphError`.
- `config.options` keys **must equal** the set of downstream node ids
  (extra or missing key each a distinct error message).
- `config.decider` ∈ `{"manual", "llm"}`; `"rule"` explicitly rejected with
  "reserved (unimplemented)".
- `pinned` (manual) and `fallback` must be members of the choice set.
- All of the above checked recursively, so a `route` inside a `parallel`
  branch is validated by the same rules.

## Designer

- Palette: `route` entry, rendered from `describe()` like the other 8
  (one door — the registry's `primitives` block).
- Outgoing edges from a route node draw as the **choice set** (dashed fan),
  distinguishing them from plain data edges at a glance.
- Node panel: decider picker, option descriptions (one per out-edge),
  `fallback` selector, `pinned` selector in manual mode.
- Last output (existing popover): `choice` + `reason`, and which edge was
  taken; pruned nodes keep their existing skipped styling, now carrying the
  `"not routed"` / `"upstream pruned"` detail.

## Tests (fakes only — repo frozen rule: no real-LLM assertions)

- Validator: options ≠ downstream set (both directions); fallback/pinned ∉ set;
  decider enum incl. reserved-`rule` rejection; <2 out-edges rejected; same
  rules hold inside a branch.
- Engine: chosen path runs; unchosen + transitive subtree skipped with
  `"not routed"`; convergence — `merge` commits only active inputs; a node
  requiring an input only the pruned branch provided → `skipped("upstream pruned")`,
  **not** failed; invalid llm choice → fallback taken (and the no-fallback
  abort path); budget consumption (decision unit, graph ceiling binding,
  budget-0 ⇒ fallback without an LLM call).
- Determinism: fixed-fake decider ⇒ identical conversation order across 5
  runs (extending the existing parallel-determinism test shape).

## Out of scope for this branch

- **Agent groups** — refined and split into two readings (2026-10-13).
  **v1 = the capability-composition (bind-then-run) agent graph**: an agent
  is a second, sibling *document* (model + MCP + skills + persona + exactly
  one `loop` child, the turn-graph) bound to the context at turn start and
  then run **unchanged** — one loop per agent. This is the near-term
  **chosen** approach, now spec'd: [agent-graph.md](agent-graph.md).
  **Deferred = the nested/routed reading**: the agent graph sequencing or
  fanning out *multiple* sub-graphs with `route` as the inter-agent
  dispatcher (serial handoff) — the larger contract extension, named not
  built (see agent-graph.md §Out of scope). `route` remains shippable
  standalone against today's primitives either way.
- **Parallel side-effecting agents** — shelved; the v0 invariant
  (conversation commits stay serial; `act` barred from parallel branches)
  is load-bearing and stays put.
- The **`rule` decider** and **runtime-supplied choice** — reserved, triggered
  by real demand.

## Build order

1. `schemas/graph.py` — port shape + validator rules, with the validator
   tests first (red → green, fakes).
2. `components/graph_loop.py` — active-dataflow walk, route resolution,
   skip events, convergence rule, budget.
3. `components/primitives/route.py` + `builtin.py` registration; `describe()`
   picks it up through the existing one-door.
4. Designer — palette, choice-set styling, panel controls, last-output
   `choice`/`reason`.
5. Docs — extend the v0 vocabulary line in `docs/graph-loop-designer.md`
   with the router clause; commit.

## Why

The repo's pitch is orchestration-as-data, drawn visually, with production
risk ≈ 0. The supervisor-and-specialists pattern is the most recognizable
multi-agent shape and the router is what makes it expressible **without the
user writing routing code** — only the specialists and their job
descriptions. Everything about it fails visibly (save time: wiring/config;
run time: choice/reason/fallback in the event log); nothing routes silently.
