# Graph loops & the Loop Designer

Status: draft — design decisions frozen for v0
Date: 2026-09-29
Supersedes: — (complements [hot-swap-components.md](hot-swap-components.md))

## Problem

In the current architecture, *which components* run is externalized to
slots/aliases/presets, but **the order they run in is still hard-coded in a
loop class** (`PlanExecuteLoop`, `DirectLoop` — `components/loops.py`).
Expressing a new topology (retry-with-critique, parallel research, branching)
requires writing a new Python `AgentLoop` and registering it. There is no way
to declare order, branching, or concurrency as *configuration*.

## Product identity

A **Loop Designer** for AIHarness: declare agent-loop orchestration as a
JSON graph — ComfyUI-style — over the existing component slots. Open-source
audience: developers learning the harness, building loops visually instead of
in code. Trusted developer audience (no customers, no auth).

The existing deployment shape is `one backend (harness) + frontends`; the
Designer is a **second frontend app** (sibling of `chat-ui`) plus purely
additive harness endpoints. LLMs stay external over HTTP.

## Threshold decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| 1 | Who is it for? | v0 internal (trusted author, graphs as JSON files); **designed for open source**: public primitive contract, doc-first | One author keeps v0 small; the contributor story must work from day one |
| 2 | Freedom vs complexity? | **Small closed vocabulary (8 primitives) + open topology** | ComfyUI's actual model: freedom comes from how you connect, not from node count. Vocabulary growth is a separate axis |
| 3 | What do graphs reference — registry or free factories? | **Registry with a new `primitives` namespace**; the 6 agent slots stay sacred; ONE discovery door (`describe()`) | One lifecycle/activation/introspection story; the canvas renders exactly what the engine can do |
| 4 | Where does it live? | **Purely additive in `app/`** (new `components/graph*` modules, `loop.graph` alias, `/api/graphs` endpoints); chat path and existing presets untouched | Production risk ≈ 0; harness stays the single engine; "deployed like any harness" |

## Frozen contract (v0)

| Area | Rule |
|------|------|
| Artifact | A graph is a **JSON document** (path, dict, or string), parsed exactly like manifests today |
| Entry | `goal` edge: the resolved `Goal` (goal/resolver or passthrough) is the standard input. Arbitrary typed inputs = v1 |
| Blackboard | `state.conversation` is **serial append-only, top-level nodes only**. Branch nodes write to `state.metadata["outputs"][<node_id>]`. Only a declared `merge` node commits branch outputs to the conversation, **in declared order** |
| Typing | Closed `requires`/`provides` string set: `context`, `goal`, `plan`, `result`, `verdict`, `merged`, `pass`. Node type also declares `side_effects: bool` |
| Validation (build-time) | Entry node = node whose unmet requires are only graph inputs; every edge type-checks against provides/requires; no cycles except through declared `control` edges; every `repeat` has `max_passes`; **`side_effects=true` nodes are rejected inside `parallel` groups**; unknown refs rejected |
| Failure | Default **fail-fast**. Per-node `on_failure: retry(n) | skip | abort`. **Branch containment (v3):** a `parallel` branch that fails at run time (executor/LLM error, a leaked `CancelledError`) degrades THAT branch to a NULL slot — siblings finish, the run records `ok`, one visible `branch_failed` event per degraded branch (on the parallel node, tagged with the branch name); a branch's `on_failure` policies still apply first. Contract violations (a binding budget ceiling, an incomplete node environment) and top-level node failures still fail the WHOLE run |
| Budgets | Unit = executor budget. Per-node, per-branch, and per-graph ceilings; graph ceiling is binding |
| Concurrency | Only inside declared `parallel` groups; `max_parallel` cap (default 4); branches run asyncio-concurrently; engine serializes all conversation commits |
| State isolation | Each execution gets a **fresh blackboard** (per-agent memory; invariant) |
| Observability | Every node emits `start` / `done` / `result` / `failure` events (JSON-serializable) — the API + canvas seam. **Since v1:** the same events are mirrored into an in-flight **live record** (`status: "running"` + partial `events`) on the same `GET /api/graphs/{id}/last-run` seam; the finished record (`ok`/`failed` + full events, answer/error) replaces it when the run ends — cleared either way. **Since v2:** `result` events additionally carry the node's **actual outputs** — `outputs` (typed, JSON-safe map) + sibling `truncated` `{key: original_char_len}` (only where cut); strings keep head+tail (16 KB each), `str()` fallback is cap-honest, 128 KB cap is **per event**; additive — old clients ignore the fields, old backends omit them (see [ai-per-node-outputs.md](ai-per-node-outputs.md)) |
| Tests | **Fakes only** (pattern: `tests/test_component_hotswap.py`). Real-LLM runs are smoke tests, never assertions. Determinism assertion: same graph ⇒ identical conversation *order* across runs |
| Safety posture | Graph = declarative; **no arbitrary code steps**; side-effect rules are correctness properties (trusted-dev auth model), not a security boundary |

## v0 vocabulary (8 primitives)

| Primitive | Maps to today | Provides | Requires |
|-----------|---------------|----------|----------|
| `prompt` | — | `context` | `goal?` |
| `research` | tool-calling executor (read-only) | `context`, `result` | `goal`, `context?` |
| `plan` | `planner.default` | `plan` | `goal`, `context?` |
| `act` | `executor.default` | `result` | `plan` or `context` |
| `critic` | `evaluator.default` + judgment | `verdict`, `pass` | `result` |
| `merge` | — | `merged` → `result` | `branches` |
| `gate` | — | `pass` | `verdict` |
| `parallel` | — | fan-in of branch `results` | ≥1 branch graph |

All 8 compose into the shipped workflow shapes: serial, retry-loop,
fan-out+merge, branch+merge.

### The 9th primitive: `route` (v1 extension)

The vocabulary is 8 — **plus `route`**, the v1 router: dynamic choice among
its wired branches (the supervisor/dispatcher shape). Three parts on three
surfaces, each failing visibly: the **choice set** = its outgoing data
edges (wiring, the canvas draws them as a dashed choice fan); the
**decider** = `manual` (pinned, zero LLM calls) or `llm` (one structured
single-choice call, `rule` reserved); the **decider inputs** = `options`
(id → description, must equal the choice set), `pinned` (manual),
`fallback` (declared, must be in the choice set). The decision costs
**exactly one executor-budget unit** (budget 0 ⇒ offline fallback, no LLM
call); the payload passes through as `context` + `result`; no side effects.
Engine semantics: **one** branch is taken; untaken subtrees are skipped
transitively with the pinned vocabulary `"not routed"`; at a re-join, a
required input with no active provider is `"upstream pruned"` — **skipped,
never failed**. A graph without a `route` node walks byte-identically to
v0, so the pinned determinism tests hold. Full contract:
[ai-routing-nodes.md](ai-routing-nodes.md).

## v0 scope

**In:**
- `schemas/graph.py` (schema + validator, all rules above)
- `components/graph_loop.py` — `GraphLoop` (alias `loop.graph`), registry client
- `components/primitives/` — the 8 nodes, each with a written 5-line contract
- `/api/graphs` (GET list, POST save by id/name, DELETE) + `activate` accepting
  `{"loop": "graph", "graph_id": "..."}`; `describe()` gains `graphs`
- One killer workflow shipped as JSON: **parallel research → merge → act → critic (≤2 retries)**
- Fake-driven tests, incl. the determinism + side-effect-rejection cases
- Real-LLM smoke script (explicit, non-asserted)

**Out (v1 candidates, listed so nobody surprises us):**
canvas UI (it *is* the later product, separate project), human-in-loop gate,
multi-goal graphs, per-run artifacts/DB persistence beyond JSON files.

## v1 (lands on the same seams, no new transport)

**Live run status (the "future canvas seam", realized):**
- The engine's event hook (`EventSink.append`, the single choke point)
  mirrors each event into a per-graph **live record** in `GraphStore`
  (in-memory, same posture as last-run records; `LIVE_EVENT_CAP` bound;
  superseded by the next run, cleared on success AND failure).
- `GET /api/graphs/{id}/last-run` (one endpoint, whole lifecycle) returns
  the live record while the run is in flight — no new routes, no new
  transport; pre-live backends simply never expose it, so older clients
  degrade to the post-run read.
- The designer polls that seam ~300ms while `POST /api/chat` is pending and
  folds the (possibly partial) event list into per-node states
  (`start→running`, `done→ok`, `failure→failed`, `retry→running`,
  `skipped`, `aborted→failed` — order-folding, so control-edge re-runs
  read correctly and parallel branches light several nodes at once):
  the **running node pulses**, the **wire feeding it animates**, and the
  finished states (`ok`/`failed`/`skipped`) persist after the run. The
  test bench shows `currently running: …`.
- SSE over the same event records is the obvious next step if sub-second
  fidelity is ever wanted — deliberately *not* built for v1.

## Contributor story (the open-source pitch)

> Add a node: write one class implementing `run(state, config) -> None` that
> reads its `requires` from the blackboard/outputs and appends/records its
> `provides`. Register it: `registry.register(PRIMITIVES, "my_node", MyNode())`
> (or one manifest JSON entry). It appears in `GET /api/components`, is
> selectable in the designer, hot-swaps like everything else, and tests write
> against the same fakes as the core.

That is the bar: **a node is a file + a registration, discovered through one
door.**

## Build order

1. **Spike** (½–1 day, throwaway OK): `parallel` + `merge` + branch-local
   outputs driving the *existing* fakes; 3 concurrent branches; assert
   conversation byte-order identical across 5 runs. If ugly → revisit contract.
2. **Vertical slice** (~3 days): schema + validator + `GraphLoop` + 8
   primitives + `/api/graphs` + the killer workflow JSON + all fake tests +
   one LLM smoke run. Done when: `POST /api/graphs` + activate + `POST
   /api/chat` on the `default` agent produces the workflow's answer, and the
   test suite proves order/validity rules.
3. **Designer app** (separate, later): second frontend (sibling of chat-ui)
   rendering `GET /api/components` + `GET /api/graphs`; the node canvas is
   the product surface and only starts once step 2 is a joy.

## Anchors in the current code

- Slots & contracts: `components/slots.py`, `components/registry.py`
- Loop contract `run(components, state)`: `components/loops.py`
- Registry client pattern (what `GraphLoop` follows): `agent.py::_snapshot_components`, `main.py::get_registry`
- Manifest/JSON machinery to imitate: `components/manifest.py`
- Fakes & test patterns: `tests/test_component_hotswap.py`
- Executed tool classes (side-effecters to exclude from forks): `tools/python_tools.py`, `tools/files.py`
- API surface to extend: `server.py` (`/api/components`, `/api/components/activate`)

## Open questions (allowed to stay open past the spike)

- Do `parallel` branch definitions live in the graph doc inline, or as named sub-graphs? (Leaning inline for v0.)
- Graph versioning on rename/edit: new id vs. in-place? (Leaning new id; JSON files are the store.)
- Is `critic`'s verdict an LLM call in v0, or reuses `evaluator` heuristics + optional LLM? (Leaning: LLM with a schema, fakes in tests.)
