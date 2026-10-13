# Your first graph

A hands-on walkthrough of the **graph loop**: read the smallest valid graph
document, activate it and run a real turn, read back the per-node event
stream, and build your own graph in the Loop Designer.

Every command below runs as-is against the compose stack, and every
"expected" response is a real capture (elided where verbose). Where the
output must be deterministic we pin it with the `offline` preset — see §9 —
so nothing in this file depends on an expensive or unavailable model.

You will learn:

- the **one door** the whole product renders (nothing is a copy of this
  document): `GET /api/components`;
- the shape of a graph document — the smallest legal one fits on a screen;
- how to activate a graph **hot-swap style** (no restart) and read its
  node-event stream back;
- how to build, save, activate, run, and inspect a graph on the canvas —
  including what a failed run looks like, because failure is a first-class
  citizen of the contract.

**Prerequisites** — Docker with `docker compose v2`. An LLM the backend can
reach (default: Ollama on the host, model
`Duggles/qwen-3-8-larger-context:latest`). No model? §9 runs every step with
the `offline` preset — zero LLM calls — and the deterministic outputs below
are exactly that mode.

---

## 1. Boot the stack

First time only, the SearXNG instance needs a local secret key (the template
is committed as `searxng/.env.example`; the real `.env` is gitignored):

```bash
cp searxng/.env.example searxng/.env
echo "SEARXNG_SECRET=$(openssl rand -hex 32)" >> searxng/.env
```

Then:

```bash
docker compose up --build
```

| Surface                     | URL                        |
|-----------------------------|----------------------------|
| Loop Designer (canvas)      | http://localhost:3020      |
| Chat UI                     | http://localhost:3010      |
| API (the engine)            | http://localhost:8000      |
| SearXNG (web search tool)   | http://localhost:8080      |

```bash
curl -s localhost:8000/health   # -> {"status":"ok"}
curl -s localhost:3020/health   # same answer, proxied through the Next server
```

Note the second line: `localhost:3020` does not reach the API directly. The
browser talks **relative** URLs to the Next server, and that server proxies
`/api/*` and `/health` to the backend container. That is why the UI works
from a browser on a different machine than the stack — nothing like
`localhost` is baked into the bundle. (Design note:
"same-origin API proxy" in `app/memory/architecture.md`.)

From here on, all `curl` examples hit the API directly at `:8000` — the same
requests, just without the proxy hop.

## 2. The one door: `GET /api/components`

This is the discovery surface. The designer renders *this response*, not a
copy — the palette, the slot list, the saved-graph library are all projections
of one document:

```bash
curl -s localhost:8000/api/components | python3 -m json.tool | head -60
```

The response has four blocks:

```jsonc
{
  "slots": {                                  // the swappable CODE (six slots)
    "loop": {
      "active": "plan_execute",
      "available": [
        {"alias": "plan_execute", "version": "1.0.0", "description": "Classic plan -> execute -> evaluate with replan"},
        {"alias": "direct",       "version": "1.0.0", "description": "Single pass: goal straight through the executor"},
        {"alias": "graph",        "version": "1.0.0", "description": "GraphLoop: run a saved JSON graph as the loop (activate with loop='graph' + graph_id)"}
      ]
    },
    "goal_resolver": { "active": "default", "available": [ /* … */ ] },
    "planner":    { "active": "default", "available": [ /* … */ ] },
    "executor":   { "active": "default", "available": [ /* … */ ] },
    "evaluator":  { "active": "default", "available": [ /* … */ ] },
    "memory":     { "active": "default", "available": [ /* … */ ] }
  },
  "presets": {                                // named slot sets (see §9)
    "offline": {"goal_resolver": "passthrough", "planner": "trivial",
                "executor": "echo", "evaluator": "always_complete",
                "memory": "default", "loop": "direct"}
    /* … default, fast … */
  },
  "primitives": [                             // the CLOSED vocabulary (9 entries)
    {"name": "prompt",   "version": "1.0.0", "description": "Injects a fixed prompt as 'context' (config.text)", "provides": ["context"], "accepts": ["goal"]},
    {"name": "research", "version": "1.0.0", "description": "Read-only context gathering via the active executor slot",
     "requires": ["goal"], "provides": ["context", "result"], "accepts": ["goal", "context"]},
    /* plan, act (side_effects: true), critic, gate, merge, parallel, route … */
  ],
  "graphs": {                                 // saved documents + which one runs
    "active": "killer-research",
    "graphs": [
      {"id": "killer-research", "name": "killer", "version": "1.0",
       "node_count": 4, "active": true, "updated_at": 1790898613.5},
      /* one row per saved document */
    ]
  }
}
```

Read it as three axes:

- **slots** — *which code runs* in each role (hot-swappable at runtime, §4);
- **primitives** — *what a graph may contain*: the closed 9-entry
  vocabulary with each node's required/optional/provided ports;
- **graphs** — *what is saved*: the documents on disk
  (`app/graphs/<id>.json`) plus the one that currently runs.

## 3. The smallest graph document

Fetch the hello sample — the minimal legal document, in full:

```bash
curl -s localhost:8000/api/graphs/sample-hello | python3 -m json.tool
```

```json
{
  "id": "sample-hello",
  "name": "sample/hello — the smallest possible graph",
  "graph": {
    "name": "sample/hello — the smallest possible graph",
    "description": "Minimal graph document: one prompt node injects framing, one act node answers through the active executor. One data edge, no branches, no retries. Every other sample adds one more contract feature on top of this exact shape.",
    "version": "1.0",
    "budget": 15,
    "nodes": {
      "frame": {
        "primitive": "prompt",
        "config": {
          "text": "You are a sharp, concise assistant. Answer in a single short paragraph, no preamble."
        }
      },
      "answer": {
        "primitive": "act",
        "config": {
          "task": "Answer the goal in one short paragraph, using the context injected above."
        }
      }
    },
    "edges": [
      { "from": "frame", "to": "answer", "type": "data" }
    ]
  }
}
```

Annotate it:

- **`nodes`** — a map `id → node`. Each node is a `primitive` (one of the 9
  from §2) + a `config` (free-form JSON, validated per primitive) + optional
  `budget` (per-node spending ceiling, in executor units) and `on_failure`
  (`"abort"` default, `"skip"`, or `"retry(n)"`).
- **`frame`** is a `prompt` node: no LLM, no slot — it just injects its
  `config.text` into the conversation as `context`.
- **`answer`** is an `act` node — the **only** node type with side effects.
  It runs the active *executor* slot against `config.task`.
- **`edges`** — data flow. One edge says: `answer` may read `frame`'s
  `context` output. (Typed: the engine checks provides/needs, and data edges
  must be acyclic. The only legal cycle in the whole contract is a *control*
  edge — §7.)
- **`budget: 15`** — the graph-level ceiling; every node's budget and every
  branch's budget nest underneath it (per-graph ceiling binds).

That's the entire core shape. Everything else in the vocabulary is a node
type added on top of it.

## 4. Activate + run — three curls

Activation is **hot-swap**: the swap takes effect on the *next*
`/api/chat`, no restart, an in-flight turn keeps the components it started
with.

**1. Activate the graph:**

```bash
curl -s -X POST localhost:8000/api/components/activate \
  -H 'content-type: application/json' \
  -d '{"loop": "graph", "graph_id": "sample-hello"}'
```

→ `200`, and the body is the same document as §2 (check
`slots.loop.active == "graph"` and `graphs.active == "sample-hello"`).

**2. One turn:**

```bash
curl -s -X POST localhost:8000/api/chat \
  -H 'content-type: application/json' \
  -d '{"message": "What is the capital of Canada?"}'
```

→ `200 {"answer": "<your LLM's answer>"}`. The answer is whatever the active
executor produced under the `frame` injection — with a real LLM that is a
short paragraph; with the `offline` preset it is the deterministic echo
(`ECHO[answer]: Answer the goal in one short paragraph, …`, §9).

**3. Read back the run record — the canvas seam:**

```bash
curl -s localhost:8000/api/graphs/sample-hello/last-run | python3 -m json.tool
```

```json
{
  "status": "ok",
  "at": 1791662525.743,
  "answer": "ECHO[answer]: Answer the goal in one short paragraph, using the context injected above.",
  "error": null,
  "events": [
    {"event": "start",   "at": 1791662525.743, "node": "frame"},
    {"event": "result",  "at": 1791662525.743, "node": "frame",
     "detail": "context",
     "outputs": {"context": "You are a sharp, concise assistant. Answer in a single short paragraph, no preamble."}},
    {"event": "done",    "at": 1791662525.743, "node": "frame"},
    {"event": "start",   "at": 1791662525.743, "node": "answer"},
    {"event": "result",  "at": 1791662525.743, "node": "answer",
     "detail": "result",
     "outputs": {"result": "ECHO[answer]: Answer the goal in one short paragraph, …"}},
    {"event": "done",    "at": 1791662525.743, "node": "answer"}
  ]
}
```

Record shape: `status` (`"ok" | "failed"`), `at`, `answer`, `error`, and
`events` — an ordered stream of what the engine actually did. That event
stream is the whole observability seam: the designer's canvas colors, the
run bench, and the "Live" status all render *this array* — no second
transport.

## 5. The event vocabulary (small, closed, meaningful)

| Event            | When                                            | Extra fields |
|------------------|-------------------------------------------------|--------------|
| `start`          | node begins executing                            | `node` |
| `result`         | node committed its outputs                       | `detail` (output key(s) committed), `outputs` (the payload), `truncated` only if a value was head+tail cut |
| `done`           | node finished normally                           | `node` |
| `retry`          | `on_failure: "retry(n)"` — next attempt          | `detail` (`"attempt 2 of 3"`) |
| `skipped`        | node was skipped                                 | `detail` = the reason, pinned vocabulary (e.g. `"not routed"`, `"upstream pruned"`) |
| `failure`        | node failed this attempt                         | `detail` = exception message |
| `aborted`        | run aborted (default `on_failure` for a failure) | `detail` |
| `control`        | a **control** edge fired (bounded retry loop)    | `detail` = `"verdict not passed -> re-run from '<node>' (pass N/M)"` |
| `branch_failed`  | one parallel branch failed at runtime and *degraded to an empty slot* — the run continues | `node` (the parallel node), `branch` (the branch name), `detail` |

Two reading rules worth internalizing:

- **`skipped` is not `failure`.** An untaken `route` branch is *skipped
  with a reason* (`"not routed"`); a failed node is a `failure` (+
  `aborted`, `retry`, or `skip` depending on its `on_failure`). The record
  tells you *why* a node didn't run.
- **A flaky branch is not a dead run.** `branch_failed` means exactly one
  branch degraded to an empty slot, its siblings finished, `merge` committed
  whatever succeeded, and the run still recorded `status: "ok"` — while a
  *contract* violation (budget exceeded, misdeclared graph) still fails the
  whole run. A defective document aborts loudly; a flaky LLM call is a
  weather event.

**The live half.** While `POST /api/chat` is still pending, the *same*
`last-run` URL serves the in-flight record:

```jsonc
{"status": "running", "at": …, "answer": null, "error": null, "events": [/* …so far */]}
```

Poll it every few hundred milliseconds while the turn is in flight (the
designer polls at 300 ms) and you see the currently-executing node appear in
real time; the finished record (`"ok"`/`"failed"`) replaces it when the run
ends. Records are in-memory per process — a restart clears them, the graph
*files* persist.

## 6. Build your own graph in the Loop Designer

Open **http://localhost:3020**. The header says *Loop Designer* with a green
backend dot. Layout: **Graphs** (library) and **Primitives** (palette) on the
left, the **canvas** in the middle with a **toolbar** above it, node
inspector + graph **Inspector** + the **run bench** on the right.

The canvas never opens empty: the initial working graph (`my-loop`) is a
sensible `research → answer` pair. Each row of the **Graphs** rail is a saved
document — `name`, then `id · N nodes`, with a `●` on the active one — and
`+ new` starts a fresh graph under a new id.

**Step by step — recreate `sample/hello` from scratch:**

1. **`+ new`** → toast: `Fresh graph loop-140322 — add nodes and save when
   ready.` (that id is `loop-` + the current HHMMSS — yours will differ; ids
   are stable `[A-Za-z0-9_-]` tokens, never edited in place).
2. **Palette → click `prompt`.** A node lands on the canvas, auto-selected,
   with a generated id (`<primitive>_N`). New `prompt` nodes come pre-seeded
   with a `config.text`; the node's panel (it is open — selection follows the
   new node) shows the **config** JSON with an **Apply config** button, plus
   **Budget** and **on_failure** (`abort (default)`, `skip`, `retry(1…5)`).
   Click away to close the panel; click the node to re-open it.
3. **Palette → click `act`.** A fresh `act` node with an empty config. In its
   node panel,
   set the config JSON to
   `{"task": "Answer the goal in one short paragraph, using the context injected above."}`
   and press **Apply config**.
4. **Wire them:** drag from `prompt_1`'s output port to `act_1`'s input
   port — a data edge appears. (Data edges are solid; the dashed `⟲ repeat`
   port on a `critic`/`gate` node is a *control* edge — drag one and the
   Inspector shows `max_passes (declared repeat ceiling)`, mandatory.)
5. **Graph settings:** the Inspector's **Settings** section holds **Name**,
   **Budget** (the graph ceiling), and **max_parallel** (branch cap — only
   meaningful with a `parallel` node). **Checks** mirrors the validator's
   structural rules client-side, so you see "this cannot be saved" before
   the API tells you.
6. **Save** (toolbar). The `unsaved` chip disappears, the badge reads
   `saved`. Saving posts the document and it is **validated first** — an
   invalid document gets a `400` and never touches disk (§8).
7. **Activate** (toolbar) → the badge reads `active`. From the *next*
   `/api/chat` onward — including the chat UI and this bench — this graph is
   the workflow.
8. **Run bench** (right rail): type a **Goal (sent as the chat message)**
   and press **Activate + run** (or **Save + activate + run** if you're
   dirty). Hint under the button: *"Runs the graph on the backend — real
   LLM, evidence only."* While it runs: `currently running: frame, answer…`
   — the live record's in-flight nodes, from §5's live half. When it ends:
   the **Answer**, any **Engine error**, and **Node events (N)** — each
   `result` row expands to the node's actual `outputs` (the click-to-read
   seam from §4).

**Per-type node panels** (the canvas adapts the inspector to the primitive):

- `parallel` — **Inline branches (JSON)**: the branch sub-graphs live in the
  node, not as separate canvas nodes (a branch is a fresh-blackboard
  sub-graph; only `merge` may commit its outputs, in declared edge order).
- `route` — a structured routing section instead of raw JSON: **decider**
  (`manual (pinned, no LLM calls)` | `llm (structured single choice)`),
  **fallback**, **pinned (manual decider)**, and a per-outgoing-edge option
  description — because a route's choice set **is its outgoing data edges**,
  and the panel rebuilds `config.options` over the current edge set so the
  config can never drift from the wiring (the validator requires exact
  equality).
- `critic` / `gate` — the dashed `⟲ repeat` control-edge port for bounded
  retry loops (§7).

## 7. The six samples = the whole vocabulary, one feature each

`GET /api/graphs` lists every saved document — the six samples below, the
shipped `killer` workflow, plus anything you have saved. The samples are
ordered as a ladder — each adds exactly one contract feature:

| Sample                | Walks                                                        | Watch in the `last-run` record |
|-----------------------|--------------------------------------------------------------|--------------------------------|
| `sample-hello`        | minimal document (`prompt → act`)                             | the §4 record |
| `sample-pipeline`     | `research → plan → act`; per-node budgets; `on_failure: "skip"` | `skipped` on the flaky research if it dies — the turn still lands an answer |
| `sample-critique`     | the retry loop: `act → critic` + a **control** edge back, `max_passes: 3` | `control` events on a retry loop; the `critic` = evaluator heuristic first, then one LLM-with-schema verdict (a real LLM is needed) |
| `sample-fanout`       | **full vocabulary**: `parallel` (2 branches) → `merge` → `act` with `retry(2)` → `critic` → `gate` (no-LLM sign-off) | branch `start/result/done` from both branches, `result` on `merge` committed in declared order, `gate` passing without a model call |
| `sample-supervisor`   | the 9th primitive: `route` with the `llm` decider — the choice set is the outgoing edges, one structured call (choice **+ mandatory reason**), declared fallback | `result` on the route node carries `outputs.choice` **and** `outputs.reason`; the untaken branch is `skipped` with `detail: "not routed"` |
| `sample-manual-route` | the same supervisor, deterministic: `manual` decider + `pinned` + `budget: 0` = **zero LLM calls on the hop** | exactly one `result` on the route node (`choice`, `reason: "manual decision: pinned branch 'notes'"`) + `skipped: "not routed"` on the other — fully offline |

`killer-research` (the active one by default) is the production-style
workflow: parallel web + local research → merge → act → critic with 2
bounded retries.

Rules of thumb: **`manual-route` is the offline/debug mode** for anything
routed (pin a branch, spend nothing); **`supervisor` is the real thing**
(one LLM hop to choose, paid for visibly); anything with a `critic` wants a
real model (its verdict is "evaluator first, LLM otherwise").

## 8. Fail on purpose — and read the failure

Four failure cases, all by design, all visible:

**(a) Invalid documents are rejected, never saved.**

```bash
# unknown primitive
curl -s -X POST localhost:8000/api/graphs -H 'content-type: application/json' \
  -d '{"id":"my-bad-graph","graph":{"budget":5,"nodes":{"oops":{"primitive":"wat","config":{}}},"edges":[]}}'
```

→ `400 {"detail": "graph node 'oops': unknown primitive 'wat' (available: prompt, research, plan, act, critic, gate, merge, parallel, route)"}`

```bash
# illegal id (valid document, but the id is not a [A-Za-z0-9_-] token)
curl -s -X POST localhost:8000/api/graphs -H 'content-type: application/json' \
  -d '{"id":"has space","graph":{"name":"x","version":"1.0","budget":5,
    "nodes":{"frame":{"primitive":"prompt","config":{"text":"hi"}},
             "answer":{"primitive":"act","config":{"task":"Answer."}}},
    "edges":[{"from":"frame","to":"answer","type":"data"}]}'
```

→ `400 {"detail": "graph id must be a [A-Za-z0-9_-] token; got 'has space'"}`

The validator (`app/schemas/graph.py`, pure, no LLM, no I/O) is the single
authority at save, activate, *and* execute — a document that cannot run
cannot even be saved.

**(b) A run can fail mid-graph; the record says where and why.**
Real capture (LLM unreachable during an offline-mode `critic` verdict):

```json
{"status": "failed", "at": …, "answer": "", "error": "ConnectError: [Errno -2] Name or service not known",
 "events": [ /* … */
   {"event": "start",  "node": "judge"},
   {"event": "failure","node": "judge", "detail": "ConnectError: [Errno -2] Name or service not known"},
   {"event": "aborted","node": "judge", "detail": "[Errno -2] Name or service not known"}
]}
```

`/api/chat` for that turn returns `500` with a JSON `detail` carrying the
same diagnostic — never a bare, bodyless 500. Compare (a): in (a) the
*document* was wrong; in (b) the *environment* was not ready. Both are
loud; neither is silent.

**(b′) A document can be structurally valid and still fail fast — budgets.**
A node with no explicit budget consumes the default of 6 executor units
(`schemas/graph.py::DEFAULT_NODE_BUDGET`), and the graph ceiling is binding.
Real capture — a valid document with `budget: 5` under an `act` node:

```text
POST /api/chat -> 500 {"detail": "GraphBudgetError: node 'answer' needs 6 executor-budget unit(s); ceiling 5 would be exceeded (0 already allocated)"}
```

and its record:

```json
{"status": "failed", "events": [{"event": "start",   "node": "frame"},
                                 {"event": "result",  "node": "frame"},
                                 {"event": "done",    "node": "frame"},
                                 {"event": "failure", "node": "answer", "detail": "GraphBudgetError: …"},
                                 {"event": "aborted", "node": "answer"}]}
```

This one is a *defect* (unlike (b)), and the contract says so by behavior:
budget violations fail the run where it stands, naming the spending node —
a flaky model is a weather event; a broken ceiling is a bug.

**(c) Deleting the active graph empties the active slot.**

```bash
curl -s -X DELETE localhost:8000/api/graphs/my-first-graph
# -> {"active": null, "graphs": [ /* … */ ]}
```

The next `/api/chat` then fails with a precise pointer:

→ `500 {"detail": "GraphLoopError: no active graph — save one (POST /api/graphs) then activate with {\"loop\": \"graph\", \"graph_id\": \"...\"}"}`

Round-trip the other way any time (save → activate → run → delete), e.g.:

```bash
curl -s -X POST localhost:8000/api/graphs -H 'content-type: application/json' -d '{
  "id": "my-first-graph", "name": "my first graph",
  "graph": {"name":"my-first-graph","version":"1.0","budget":10,
    "nodes":{"frame":{"primitive":"prompt","config":{"text":"Be extremely brief."}},
             "answer":{"primitive":"act","config":{"task":"Answer in a single sentence."}}},
    "edges":[{"from":"frame","to":"answer","type":"data"}]}'
# -> 200, graph now in the list; then activate + run + delete as above
```

## 9. No LLM? Dry-run everything — the `offline` preset

The registry ships deterministic, LLM-free implementations for every slot
(see the `presets` block in §2): `offline` = passthrough goal, trivial
planner, **echo executor**, always-complete evaluator. With it, the graph
loop runs fully offline and the outputs are *exact* — which is why every
deterministic "expected" in this document is an offline-mode capture.

```bash
curl -s -X POST localhost:8000/api/components/activate \
  -H 'content-type: application/json' -d '{"preset": "offline"}'
```

Then §4 verbatim, with a `manual`-decider graph (zero LLM calls anywhere):

```bash
curl -s -X POST localhost:8000/api/components/activate \
  -H 'content-type: application/json' \
  -d '{"loop": "graph", "graph_id": "sample-manual-route"}'
curl -s -X POST localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"message": "Explain what the graph loop is."}'
# -> {"answer": "ECHO[notes]: Write a clear, self-contained note answering the goal, grounded in any context provided."}
```

and its record shows the pinned decision, the untaken branch, and the skip
reason — all readable:

```json
{"event": "result", "node": "dispatch", "detail": "choice, context, reason, result",
 "outputs": {"choice": "notes", "reason": "manual decision: pinned branch 'notes'", "…": "…"}},
{"event": "skipped", "node": "blueprint", "detail": "not routed"}
```

Caveat (by design): a `critic` whose heuristic is inconclusive *does* make
one LLM judgment call — under `offline` that is a deliberate, visible
fail-fast (the §8(b) shape), which is exactly the contract saying "a node
that needs a model reports needing a model".

**Back to real models:** activate the `default` preset and any graph id —
two curls, still no restart:

```bash
curl -s -X POST localhost:8000/api/components/activate -d '{"preset": "default"}' -H 'content-type: application/json'
```

## Where the rules actually live

This tutorial is the *walk*; the *contract* and its design rationale are:

| Doc                              | Covers |
|----------------------------------|--------|
| `docs/graph-loop-designer.md`    | the frozen v0 contract (ports, edge rules, budgets, failure semantics) + canvas seam |
| `docs/ai-routing-nodes.md`       | v1: `route` — the 9th primitive, deciders, choice-set rules, skip reasons |
| `docs/ai-per-node-outputs.md`    | v2: per-node outputs on the `result` event (caps, `truncated`, click-to-read) |
| `docs/hot-swap-components.md`    | slots / aliases / presets — the swap semantics behind §4 and §9 |
| `app/schemas/graph.py`           | the authority: pure validator, closed vocabulary (what the canvas *cannot* draw) |
| `app/graphs/sample-*.json`       | the six samples above, with a `name`/`description` that explains each |
| `app/graph_smoke.py`             | a real-LLM end-to-end smoke run (prints answer + every node event) |

And the memory convention this repo follows: when a behavior like the
`critic` heuristic or the `branch_failed` containment rule bites you, the
`why` is in `app/memory/architecture.md` — read it before refactoring around
it.
