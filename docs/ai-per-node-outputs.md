# AI per-node outputs (design)

**Branch:** `ai-per-node-outputs` — **Status:** implemented 2026-10-06 (engine + designer + tests green; 99 Python / 7 designer tests passing)

## The gap

Every graph node already produces concrete outputs (research `result`/`context`,
`plan` list, act `result`, `verdict` dict, `merged` list — the `state.out(...)`
calls in `components/primitives/`). But after a run, the only thing that
survives is:

- the `result` event, whose `detail` is a one-line
  `", ".join(sorted(outputs))` name list (`graph_loop.py`, `_run_node`) —
  **no values**;
- the final answer (`_pick_answer`) and per-node status highlights
  (starting from `done`, `failed`, `skipped`, `running`).

There is no way to see *what the AI actually produced for each node* —
not on the canvas, not in the Inspector, not in the test bench. That is this
feature.

## Goal

When a node is **clicked after a run** (or live, while it runs), the designer
shows that node's actual output(s) — typed and readable. Additive to the
frozen v0 contract; no new routes, no new transport, no SSE.

## Design

### 1. Engine — `app/components/graph_loop.py`

The `result` event gains an `outputs` payload:

```python
{"event": "result", "node": "research_1", "at": ...,
 "detail": "context, result",            # kept — backward compatibility
 "outputs": {"result": "…text…", "context": "…text…"},
 "truncated": {"context": 234567}}       # sibling map: key -> original char length;
                                         # present only where a key was cut
```

**Serializer (`_jsonable`) — rules that matter:**

- Recursive over dict/list. Strings keep head **and** tail: first **16 KB**
  + last **16 KB** with a `… (dropped N chars) …` middle marker. Front-only
  truncation was explicitly rejected: for LLM output the conclusion
  (verdict reason, final decision, closing answer) lives at the *tail*,
  which is exactly where this feature earns its keep.
- The `str()` fallback for non-JSON types runs THROUGH the same per-string
  truncation, and every emitted byte (native or fallback) counts against the
  event total — the fallback cannot defeat the caps.
- Caps: **32 KB per string** (16 head / 16 tail), **128 KB per `result`
  EVENT** — deliberately per-event, not per-node: control-edge re-runs emit
  fresh events, and the per-node multiplier is already user-declared
  (`max_passes`). A cumulative per-node ledger was rejected as overkill
  (see Backlog).
- `truncated` is a sibling map `{key: original_len}` (present only when
  something was cut), so `outputs` stays a clean renderable map and the UI
  can show per-output "truncated (234 KB total)" instead of one decorative
  event-level boolean.

`EventSink` is unchanged — it is already the single choke point, and the caps
bound the cost of every event. `failure`/`skipped`/`retry`/`control` events
are unchanged (`failure`'s `detail` error string already suffices).

**Why capped, not full:** the record is in-memory (storage is cheap); the
real cost is the designer's ~300 ms live poll re-fetching the whole record
on every tick. The caps make worst-case event size constant-bounded (same
reasoning as `LIVE_EVENT_CAP = 500`); the *growth of the record with run
length* is accepted in v0 (see Backlog: incremental reads). "Full output"
is a one-constant escalation (or a future `?full=1`) — deliberately
deferred; the serializer already knows where it cut.

**Exposure note:** this widens what is served by `GET /api/graphs/{id}/last-run`
(raw LLM text, web-search content) on an endpoint behind
`allow_origins=["*"]`. Accepted under the repo's declared posture ("trusted-dev
auth model, not a security boundary" — docs/graph-loop-designer.md); origin
tightening is a separate hardening task (Backlog).

### 2. Store / API — no route changes

The payload rides the existing `events` array on the live + finished
`last-run` record, served by `GET /api/graphs/{id}/last-run`. Old clients
ignore the new field; an old backend degrades (field absent).

### 3. Designer — `designer/app/...`

- `lib/types.ts`: `RunEvent.outputs?: Record<string, unknown>`;
  `RunEvent.truncated?: Record<string, number>` (key -> original length).
- `lib/run.ts`: new fold `deriveNodeOutputs(events)` →
  `Map<nodeId, Record<type, value>>`, keyed on the **bare** node id with
  last `result` per node winning — deliberately the SAME id space as the
  existing `deriveNodeStates`, so the two folds agree on the same event
  stream. `branch` tags are *displayed* (event row labels), not used as
  lookup keys (branch-aware keying is Backlog).
- **On-node inspector popover** (the "visible on click" half): clicking a
  node opens a popover anchored to that node (portaled to `<body>` so it
  escapes the canvas transform/zoom) carrying a **Last output** section,
  rendered per type — plain text, `plan` as an ordered list, `verdict` as
  pass/fail pill + reason, `merged` as a list; a per-output "truncated
  (N KB total)" badge from the event's `truncated` map; and when an output
  string equals the record's top-level `answer`, a "graph answer" tag
  (dedupes the visible answer, ~one comparison). Selection drives it: a
  clean click SELECTS the node → popover opens; `Escape` or clicking
  elsewhere (canvas / another node / an edge) closes it, and dragging a node
  dismisses the popover so it never follows the drag. The same popover
  carries the node's editable config (id / budget / `on_failure` / config
  JSON / inline branches). The fixed right rail is now a **slim rail**
  for the genuinely-global surface only — graph settings + structural
  checks + the edge panel; per-node config + last output moved on to the
  node. Shared component: `components/NodePanel.tsx` (renderer + editor).
- **Test bench**: `result` event rows become expandable, showing the payload
  inline (labeled with the `branch` tag when present).

## Tests (fakes only — repo convention: no real-LLM assertions)

- Engine: fake-graph run asserts `outputs` carries exact values; a >32 KB
  string keeps head + tail with the `… (dropped N chars) …` marker and the
  key lands in the `truncated` map at its original length.
- Engine: a non-JSON (unserializable) value's `str()` fallback is itself
  truncated and counts toward the per-event total (cap-honest fallback).
- Engine: a control-edge re-run emits a *fresh* event with a *fresh* 128 KB
  event cap (confirms the cap is per-event, not per-node).
- Store: live mirror keeps `outputs` + `truncated` on the in-flight record;
  finished record does too.
- Designer: `deriveNodeOutputs` — re-run overwrites last; bare-node-id keys
  (branch events fold under the same id); `branch:node` is NOT a key.

## Docs

`docs/graph-loop-designer.md` observability line: extend the event contract
with the additive `outputs`/`truncated` fields.

## Design log (devil's advocate pass, 2026-10-06)

Each concern was weighed for whether the tradeoff is worth implementing *now*:

| # | Concern | Decision |
|---|---------|----------|
| 1 | Front-side truncation drops the conclusion (the tail matters for LLM output) | **Fix now** — head+tail (16 KB each) with a `… (dropped N chars) …` marker |
| 2 | "128 KB per node" is really "per event"; per-node total is unbounded across re-runs | **Do not build** a per-node ledger — rename honestly to *per-event*, note `max_passes` bounds the multiplier (in Backlog if it ever bites) |
| 3 | Whole-record re-download on every ~300 ms poll is the real cost; the cap is a band-aid suppressing the incremental-read fix | **Defer** — named follow-up, paired with a future `?full=1` (Backlog) |
| 4 | `str()` fallback can defeat both caps (1 MB object → 1 MB string) | **Fix now** — fallback runs through the same truncation and counts toward the event total |
| 5 | Single `truncated` boolean is too coarse for the Inspector UI | **Fix now (cheaper variant)** — sibling map `{key: original_len}`, keep `outputs` a clean render map |
| 6 | `branch:nodeId` keys are dead for the canvas (branch nodes aren't selectable) and disagree with existing `deriveNodeStates` | **Do not build** — align both folds on the bare node id (last-wins), surface a known limitation (Backlog) |
| 7 | Output is served over `allow_origins=["*"]`; exposure widened, unacknowledged | **Acknowledge now** (exposure note above); origin tightening is a separate hardening task (Backlog) |
| 8 | Final-node `output` duplicates the top-level `answer`, burns cap | **Engine: no special-case** — dedupe in the UI with a "graph answer" tag (~one comparison) |

Do-now (one afternoon): #1, #4, #5 — all inside the same `_jsonable` + tests.
Doc-only: #2, #7, #8. Defer: #3, #6.

## Backlog (technical debt — intentionally out of scope for this feature)

Each item: what it is, why deferred, the trigger that would make it worth doing.

1. **Branch-aware observability.** Branch events fold under the *bare* node id
   (last `result` wins), consistent with `deriveNodeStates`. Known limitation:
   a branch-internal node and a top-level node that SHARE an id will
   highlight/output each other (this ambiguity already exists in the live
   highlight today). A real fix needs a branch-scoped key in *both* folds plus
   branch-internal nodes being SELECTABLE on the canvas (they're not canvas
   nodes today — they live inline in the node inspector popover's `BranchEditor`).
   **Trigger:** multi-branch graphs with shared node ids become routine.
2. **Incremental last-run reads.** `GET .../last-run` returns the whole record
   each poll. Add a `?since=<n>` event cursor (or ETag/If-None-Match) so the
   designer fetches only new events; merge client-side. Pair with an optional
   `?full=1` to lift the caps for a one-shot untruncated snapshot.
   **Trigger:** long / multi-run dev sessions feel slow, or records grow past
   a few MB and the 300 ms cadence starts to hurt.
3. **CORS hardening.** `allow_origins=["*"]` + real LLM/web payloads means any
   local page (or devtools) can read them. Accepted under the trusted-dev
   posture for v0. Real fix = per-environment origin lists; the current
   docker-compose dev flow depends on the open mode, so this is a migration.
   **Trigger:** the server is ever exposed beyond localhost.
4. **Per-node output-cap ledger.** Per-event caps × the user-declared
   `max_passes` are the effective per-node bound. A cumulative per-node
   ledger was rejected as overkill. **Trigger:** real graphs with large
   `max_passes` and large outputs that strain the in-memory record.

## Build order

1. **Engine:** `_jsonable` (head+tail, cap-honest fallback, `truncated` map)
   + `outputs` on `result` + fakes tests.
2. **Designer:** `types` + `deriveNodeOutputs` (bare-id fold) + Inspector
   "Last output" section (+ "graph answer" tag, `truncated` per-output badge)
   + bench expand.
3. **Docs:** extend `docs/graph-loop-designer.md` contract line; commit.
4. **Backlog:** file items above as technical debt (do NOT build this branch).
