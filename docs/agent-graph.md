# Agent graph: the capability-composition layer above the loop

**Status:** implemented (v1) — contract, store + the `/api/agents` door, the
MCP client/adapter/pool, bind-then-run (model/MCP/skills/persona onto the
existing seams, visible-failure model), the chat panel's list/enable/test MCP
surface, **the designer's read-only Agents library** (one line per agent;
the bundle's shape on expand; the `loop` ref expandable into the referenced
turn-graph, "open in canvas"), **and the designer's authoring surface** — the
**agent node on the canvas** (add/edit/save/activate/delete/test-MCP over the
`/api/agents` door; the loop child wired as a saved-graph `ref` or built-in
`alias`; the backend validator stays the authority), **and the agent-OWNED
loop** — the node OWNS its loop's authoring: a fresh node is born with its
loop slot naming the canvas's own graph, the inspector builds the loop
**inline** over the canvas's shared working state (nodes, wires, the repeat),
the card shows the loop as a live preview; on the backend the graph store
carries loop `owner`, `POST /api/agents/{id}/loop` builds the agent's private
loop (ref can never dangle), and deleting the agent cascades its owned loops,
**and the delete + deactivate paths** — deleting the BOUND agent rolls the
shared pipeline back to its pre-bind snapshot (loop slot + active graph,
model, MCP adapters, settings, persona) so a deleted agent never keeps
running the next turn, and `POST /api/agents/deactivate` does the SAME
rollback for a document the author wants to keep (the active pointer clears,
the saved agent survives, re-activatable any time).
Build order 1–9 is complete; this document stays the source of truth for v1 —
and names what comes next (the nested/routed reading) as the roadmap.
**Date:** 2026-10-13 — Supersedes: the "agent groups" note in
[ai-routing-nodes.md](ai-routing-nodes.md#out-of-scope-for-this-branch)
(the *nested/routed* reading of it, which is deferred here).
Complements [graph-loop-designer.md](graph-loop-designer.md) and
[hot-swap-components.md](hot-swap-components.md).

An **agent** is a *second, sibling graph document* — a **capability
composition graph** — whose one distinguished child, `loop`, **is** the
turn-graph you already have (a saved graph id, or a loop alias). MCP
connections and skills are **sibling nodes you wire in and out**. Bind
them together at turn start, then run the existing loop **unchanged**.

## The gap

Today an "agent" is whatever `get_registry()` hard-wires: one LLM
(`OllamaProvider`), one fixed tool set (`ALL_TOOLS` in
[main.py](../app/main.py)), one persona file (`AGENTS.md` via
`AgentConfig.agent_path`), and one active loop alias. To give a *different*
agent a different model, a different MCP server, or different skill
instructions, you edit Python and restart. There is no *document* that says
"this agent has these tools, this persona, this loop" — the turn-graph
(orchestration) and the capability bundle (what the agent *is*) are
conflated into one hard-wired context.

## The feature — and the load-bearing distinction

The turn graph (the 9-primitive vocabulary) has a **dataflow** shape: nodes
produce an LLM token stream (`goal → context → plan → result`) and execute
**inline, in order, per turn**, under budgets. MCP and skills do the
opposite: they **attach tools and instructions**. They carry no
`goal/context/plan/result` ports. Forcing them into the turn-graph contract
would mean inventing a fake port type just to satisfy the validator.

So the agent graph is a **parallel document type** — closed vocabulary,
validated JSON, stored as files, hot-swapped, rendered by the designer,
one discovery door — but its topology is **a capability bundle around one
distinguished executable child**, because capabilities compose as a **set**
(not a stream). The only real "edge" is `agent → loop`.

```
agent/  (a graph document, one parent + typed children)
├── prompt     (optional)    agent system persona
├── model      (optional)    inference override
├── mcp[]      (0..n)        tool sources — the only node type with a live connection
├── skill[]    (0..n)        instruction fragments (+ optional bundled tools)
├── settings   (optional)    slot-alias overrides (e.g. memory: trimmed)
└── loop       (required, exactly one)  the turn-graph subgraph — the ONLY node that runs
```

This reuses the *machinery* (store, validator, `describe()` block, activation,
designer) rather than inventing new transport, exactly how the turn-graph
added a `primitives` namespace without becoming a 7th slot. **Six slots stay
sacred; the agent graph is a document, not a slot.**

## Closed vocabulary (v1)

| Node | Required | What it does **at bind time** | Fails |
|---|---|---|---|
| `loop` | **exactly one** | the turn to run: `{ "ref": <graph_id> }` (a saved turn-graph) **or** `{ "alias": "plan_execute" \| "direct" }` | save & bind: must be a saved id or a known loop alias; an agent with no loop can't run a turn → rejected |
| `mcp[]` | 0..n | for each entry, get-or-create a pooled `McpConnection` (keyed by `alias`), translate its tools into the harness `Tool` shape, merge into the per-agent `ToolRegistry` | save: closed `transport` enum, required field per transport; bind: connection error is a **visible** bind failure, not a silent empty toolset |
| `skill[]` | 0..n | load the instruction text (into the persona, before the conversation); register any declared `tools` | save: `path` must exist under `app/` and be text; bind: unreadable file → visible bind failure |
| `model` | 0..1 | set `ctx.llm` (provider + id + params) for this agent's turns | save: closed provider enum; bind: provider construction error → visible |
| `prompt` | 0..1 | agent system persona — **replaces** the `AGENTS.md` default | save: non-empty string |
| `settings` | 0..1 | slot-alias overrides consumed by the preset/snapshot (e.g. `{"memory": "trimmed"}`) | save: keys ⊆ the six slot names; bind: unknown alias → visible |

`mcp` and `skill` are **arrays of entries** (a set), not pipeline nodes —
there is no ordering or typed dataflow between them, and pretending otherwise
would invent ports that mean nothing.

Example:

```jsonc
// agents/researcher.json   (validated at save; a sibling of app/graphs/<id>.json)
{
  "id": "researcher",
  "name": "Deep Researcher",
  "prompt": "You are a research specialist. Cite sources. Stay on the ask.",
  "model": { "provider": "ollama", "id": "Duggles/qwen-3-8-larger-context:latest" },
  "mcp": [
    { "alias": "web",   "transport": "sse",   "url": "http://searxng:8080/mcp" },
    { "alias": "files", "transport": "stdio", "command": ["python", "-m", "local_files_mcp"] }
  ],
  "skill": [
    { "path": "skills/citation.md" },
    { "path": "skills/pplp.md", "tools": ["read_pdf_page", "get_pdf_info"] }
  ],
  "settings": { "memory": "trimmed" },
  "loop": { "ref": "killer-research" }   // ← the subgraph (a saved turn-graph)
}
```

## Bind-then-run (the crux — and why the hot path stays untouched)

**Two phases, one new one:**

- **Bind** *(new)* — at `POST /api/agents/<id>/activate`, or lazily at the
  first turn: build/refresh the `ComponentContext` **from the document**,
  each piece landing on an already-existing seam (no new transport):
  - `loop` → exactly what the activate route does today
    ([server.py](../app/server.py)): `ref` ⇒ `registry.activate(LOOP, "graph")`
    **plus** `graph_store.set_active(ref)` — `GraphLoop.run()` resolves the
    doc via `store.active_id`, so the ref must land on that store;
    `alias` ⇒ `registry.activate(LOOP, alias)` with
    `alias ∈ {plan_execute, direct}` (the loop-slot aliases in
    [builtin.py](../app/components/builtin.py); `graph` is reached by
    `ref`, so it is not an alias value).
    Note: the shared `GraphStore` active id is process-wide, so activating
    an agent flips the active graph — the *same* semantics as activating a
    graph directly today;
  - `model` → `ctx.llm` (provider + id + params);
  - `prompt` + `skill[]` → the **composed persona**. `AgentRuntime` reads
    `config.agent_path` at construction ([agent.py](../app/agent.py),
    `load_agent_instructions`), and `build_agent()` runs **per turn** in
    `/api/chat` — so bind only has to point
    [`AgentConfig.agent_path`](../app/agent_config.py) at the composed text
    (an inline field on the same config, or a materialized persona file);
    the read path is never touched;
  - `mcp[]` → pooled connections, tools merged into the context's
    `ToolRegistry` — the executor already reads `ctx.tools`
    ([builtin.py](../app/components/builtin.py), `make_executor`);
  - `settings` → one `registry.activate(slot, alias)` per entry (the slot
    path of the activate route), e.g. `memory: trimmed`.
- **Run** — *nothing new.* The existing per-turn snapshot in
  [agent.py](../app/agent.py) (`_snapshot_components` →
  `loop.run(components, state)`) proceeds **byte-for-byte as today**. The
  `loop` pointer only selects *which* turn-graph/alias runs.

The agent graph is therefore a **configuration layer that the existing
slot/turn-graph machinery already enforces** — no new execution path, no
new event kind, no new transport. A mid-activation swap lands on the *next*
turn (the pinned per-turn-snapshot semantics), exactly like every other slot
swap.

### Connection pooling (the one genuinely new runtime piece)

An stdio/SSE MCP server must **not** be spawned per turn. `app/mcp/pool.py`
holds a process-shared `McpPool` keyed by `alias`: `get(alias)` returns a
live connection or spawns/opens one; tools are translated once; `reset(alias)`
is called on re-activate (drop + re-spawn with the new config); `drain()` on
shutdown. The adapter's call path is:

```
agent turn → executor (LLM) picks a tool → McpToolAdapter.__call__(args)
          → McpPool.get(alias).call_tool(name, args) → return string
```

The MCP tool is just another entry in the `ToolRegistry` the executor already
iterates — `act`/`research` nodes call it with zero special-casing.

### Skills

A skill = `{ path }` (markdown instructions) **or** `{ path, tools: [...] }`
(instructions + bundled tools). At bind: read the file, inject into the
persona (alongside `prompt`/`AGENTS.md`, before the conversation), register
the declared tools. It is deliberately a *separate* mechanism from the
`memory/` knowledge base — a skill is **per-agent identity**, memory is
**shared retrieval**.

## Decisions (threshold table)

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Slot vs document? | **Document** (parallel to turn-graph), not a 7th slot | Capability attach ≠ dataflow; keeps the six slots sacred and the 9-primitive contract closed |
| 2 | Where does MCP live? | **Backend**, pooled connections; *never* the browser | A stdio/SSE server can't run in a browser; the turn is one blocking `POST /api/chat` with no in-band tool-call channel; secrets stay server-side; keeps the fake-test oracle intact |
| 3 | Bind or run per turn? | **Bind** at activation/turn start; **run** the existing loop unchanged | Zero new execution path; the hot path and its pinned determinism tests stay intact |
| 4 | One loop or many? | **Exactly one** `loop` child (a "bind then run *one* loop") | 90% of the value; many-subgraph sequencing is the deferred nested reading (§Out of scope) |
| 5 | How do you test it? | **Fakes only**, red→green, per the repo's frozen rule | A fake MCP server + fake skill pin "bind merges tools"; no real server/LLM assertion |

## Anchors in the current code (what each piece plugs into)

| Concern | Existing seam to extend |
|---|---|
| Contract + validator | new `app/schemas/agent.py`, pure/stdlib, **sibling** of [`schemas/graph.py`](../app/schemas/graph.py) — `AgentError(ValueError)` + `validate_agent(doc) → AgentDoc` + `parse_agent(source)`, mirroring `GraphError`/`validate_graph`/`parse_graph` |
| Store (files are the store) | `app/components/agent_store.py`, mirroring [`components/graph_store.py`](../app/components/graph_store.py) — `agents/<id>.json` + `_meta.json`(active); `save` validates before persist; `describe_agents`; `reset_agent_store()` test helper mirroring `reset_graph_store()` |
| Bind to context | [`main.py::get_registry()`](../app/main.py) + [`agent.py::_snapshot_components`](../app/agent.py): the active agent doc feeds `ComponentContext` + the per-turn snapshot |
| Persona override | `AgentRuntime` reads [`agent_config.py::agent_path`](../app/agent_config.py) at construction and `build_agent()` runs **per turn** in `/api/chat` — so `prompt` + `skill[]` land as the composed persona by pointing `agent_path` at it; no read-path change |
| Tool merge | `ToolRegistry` in the context; MCP adapter registers like any `@tool` in [`ALL_TOOLS`](../app/main.py) |
| Discovery door | `GET /api/agents` (+ `/{id}`, DELETE, `/{id}/activate`, `deactivate`, `/{id}/mcp/test` — the panel's "test" opens a fresh connection and lists the advertised tools); `describe()` gains an **`agents`** block, parallel to its `graphs` block |
| MCP adapter | new `app/mcp/` (`client.py`, `adapter.py`, `pool.py`) |
| Config surface (frontend) | the chat panel's ComponentPanel-style list/enable/**test** MCP, the [designer](../designer/)'s read-only **Agents library** (one line per agent, the bundle's shape on expand, the `loop` ref expandable into the referenced turn-graph), **and the designer's agent-node authoring surface** — `AgentCard`/`AgentPanel` over the REST door, the `lib/agentNode.ts` draft contract, and the `page.tsx` wiring (add / edit-on-canvas / rename / save / activate / delete / test-MCP); `designer/app/components/AgentLibrary.tsx` + `lib/agents.ts` + `lib/agentNode.ts` |

## Validator rules (`app/schemas/agent.py` — `validate_agent(doc) → AgentDoc`, pure/stdlib)

- **Exactly one `loop`**, either a saved `ref` or a known `alias`; missing/
  multiple → `AgentError`. (An agent with no loop can't run a turn.)
- `mcp[]`: `alias` unique within the doc; `transport` ∈ `{"stdio","sse","http"}`;
  `stdio` ⇒ `command` present; `sse|http` ⇒ `url` present.
- `skill[]`: `path` exists under `app/` and is text; `tools` (optional) is a
  list of registered tool names.
- `model`: `provider` ∈ `{"ollama"}` for v1; `id` non-empty.
- `prompt`: non-empty string.
- `settings`: keys ⊆ the six slot names.
- Unknown top-level key → `AgentError` (closed vocabulary; nothing routes
  silently).

## The agent node on the canvas (the designer's authoring surface)

The designer canvas is a **design** surface over *two* document families:
the turn-graph (the closed 9-primitive dataflow vocabulary) and the agent
document (the capability bundle above it). The **agent node** is the canvas's
rendering of the *second* family — a positional, editable draft of one agent
document, saved through the same closed door and validator (the backend
remains the authority; a 400 is the honest save failure). It is reached the
way every node is: the Palette's **+ add agent** (a fresh, valid minimal
draft), or the Agents library's **edit on canvas →** (a saved document loaded
into the draft, starting in sync with it). The card's footer tracks the
draft-vs-saved diff, and the in-node inspector is where Save / Activate /
Delete-saved / Test-MCP hit the door:

| Field | Shape | Rule (mirrors `schemas/agent.py`) |
|---|---|---|
| identity | `id` (its saved-agent REST id) + canvas `x,y` | `id` is a `[A-Za-z0-9_-]` token (the store's rule); positions are canvas-only, never persisted into the document |
| `name?` | non-empty string when set | display name only |
| `prompt?` | non-empty string when set | persona (replaces `AGENTS.md`) |
| `model?` | `{ provider: "ollama", id }` | closed provider enum (v1) |
| `mcp[]` | `{ alias, transport, url\|command }` | alias unique per document; `stdio` ⇒ `command`; `sse/http` ⇒ `url` |
| `skill[]` | `{ path, tools? }` | `path` under the app root (the backend decides at save time) |
| `settings?` | `slot → alias` | keys ⊆ the six slot names |
| `loop` slot | `{ ref: <saved-graph-id> }` **XOR** `{ alias: "plan_execute" \| "direct" }` | exactly one — never both, never neither (the one child that runs); a `ref` naming **this canvas's own graph** is saved first, automatically, so the author no longer does a manual "save the loop before the agent" step |

Invariants — the node is a **design-layer concern, not a slot**:

- The agent node **never enters the turn-graph document**: the canvas's
  `toDoc()` keeps emitting 9-primitive nodes and edges only; an agent node
  serializes to exactly one agent document (`draftToDoc`) for
  `POST /api/agents`. Nothing routes silently into the loop.
- The agent node has **no dataflow ports** — it produces/consumes no
  `goal/context/plan/result`. Its only child, `loop`, is rendered as a
  **ref/alias slot on the card** (a chip naming the saved graph id, or the
  built-in alias), because the wire *agent → loop* is a **reference**, never
  an in-canvas edge; canvas edges connect 9-primitive nodes only.
- `checkAgentDraft()` is a **preflight mirror** of the validator's cheap
  rules (loop XOR + known aliases, transport pairing, alias uniqueness,
  settings slot keys, provider enum) — surfaced before save, never a new
  authority.
- **Save-order (the "save the loop before the agent" step is UX, not a
  rule):** the backend requires `loop.ref` to name a *persisted* graph
  (the store passes its known ids to the validator), so the only graph the
  canvas can still change is the one ON the canvas. `graphToSaveBeforeAgent`
  pins that decision: a ref naming the canvas's own graph ⇒ `saveAgentNode`
  persists that canvas document **first** (an idempotent overwrite — the
  graph id is unchanged, so the ref stays honest), then the agent; a ref to
  another graph (necessarily already saved — it was loaded from the store)
  or an `alias` loop ⇒ nothing precedes the agent save. Every agent action
  (Save, Activate, Test-MCP) funnels through that one seam, and the
  success toast + busy label name the extra save when it happens.
- Nothing in the node touches `schemas/graph.py`, `components/primitives/`,
  the 9-primitive vocabulary, or the pinned per-turn determinism oracle.

## The loop you own — the node OWNS its loop's authoring

The `loop` child stopped being a label. v1 UX: **the agent node owns the
authoring of the loop it runs.**

- **Born with its loop.** `newAgentDraft(canvasGraphId?)` (pinned in
  `agentNode.test.mjs`): with a canvas graph id (the normal case), the fresh
  node's loop slot is a `ref` **to the canvas's own graph** — the canvas is
  where loops are built, and the agent is its border. Without a canvas id
  (no working graph) it falls back to the built-in `alias` loop — still the
  valid minimal doc `{ "loop": { "alias": "plan_execute" } }`, nothing
  invented.
- **The inspector builds inline.** When the slot names this canvas's graph,
  the inspector (AgentPanel) shows the **`LoopBuilder`** instead of a
  read-only preview: add a primitive node · rename (draft + commit, edges
  re-point) · remove node; add a wire (data or the `⟲ repeat` — legal only
  out of a verdict node, the add-edge row enforces it) · remove a wire · set
  the repeat's `max_passes`. It edits **the SAME working graph the canvas
  renders** — through page.tsx's shared mutation handlers, so the builder is
  pure control surface and edits land where the author already looks.
- **The card SHOWS the loop.** `LoopPreview` renders the loop's node chips
  (+ edge count, capped for card width): **LIVE** from the canvas's working
  state when the ref names this canvas's graph (no fetch —
  `describeLoopCanvas`, the same `LoopView` shape as the fetched document,
  pinned against `describeLoopGraph`), otherwise fetched once from the
  saved graph (`getGraph` + `describeLoopGraph`, cached per ref) with an
  "open in canvas →" bridge. VISUAL containment, not a contract change: the
  document stays `loop: {ref}` — shareable, testable, independent.

**Ownership on the backend (the one store-level lifecycle field).** A graph
document may carry `owner: <agent-id>` — the agent that OWNS this (private)
loop. It rides on the document itself (files are the store), is shape-checked
at save (non-token ⇒ `GraphError` ⇒ 400), and surfaces in `GET /api/graphs`
(`owner`, `null` for shared loops). The v0 turn-graph contract is untouched
(the graph validator ignores `owner`; the closed 9-primitive vocabulary is
closed). The seams, all in `components/graph_store.py` + `server.py`:

| Door | Behavior |
|---|---|
| `POST /api/agents/{id}/loop` | **builds the agent's private loop**: saves a loop doc (a supplied validated graph, or the `DEFAULT_LOOP_SEED` one-node starting point) with `owner` **stamped by the door**, and points the agent's `loop.ref` at it in the same action — created together ⇒ the ref can **never dangle**. Idempotent 1:1 — reuse the agent's existing owned loop; a second one is never created. A loop `owner` that is not the agent's (or not omitted) ⇒ 400. |
| `GET /api/graphs` | the loop lists with its `owner` (or `null` = shared). |
| `DELETE /api/agents/{id}` | the agent's **owned** loops cascade away (no private loop left dangling after its agent); a loop the agent merely **referenced** (shared, no owner) **survives** — a reference does not own. **AND** when this agent is the BOUND one, the bind is detached: the shared seams it mutated (loop slot + active graph, model, MCP adapters, settings slots, persona path) are restored to the pre-bind snapshot (the detach runs before the cascade, so a pre-bind active graph is restored while it still exists and the cascade then clears it). Deleting an agent that is NOT the bound one changes no runtime state. |
| `POST /api/agents/deactivate` | the **deactivate door**: the active pointer clears and the live bind rolls back to the pre-bind snapshot — the SAME single-level undo as the delete path, minus the file removal. The saved document **survives** (deactivate ≠ delete) and is re-activatable at any time; the next turn runs the pre-bind pipeline. Idempotent: nothing active + nothing bound ⇒ a 200 no-op. Returns the family shape `{active, agents: [...]}` (active now null). |

Activation is unchanged: bind-then-run resolves the ref to the agent's owned
loop and runs it through the EXISTING `graph` slot (pinned by
`tests/test_agent_loop_ownership.py`: `active_alias(LOOP) == "graph"` and
`graph_store.active_id` is the owned loop — no new execution path).

The two authoring surfaces agree on the rule: **a ref to the canvas's own
graph = the node's; a ref to another saved graph = shared** (several agents
can point at one loop — that stays the reference reading); the inline
builder only opens in the first case.

## Failure model (consistent with the turn-graph)

- **Save time:** invalid doc ⇒ 400, never written (identical to
  `graph_store.save`).
- **Bind time:** a connection/model/persona that can't be built is a
  **visible bind failure** (an error on the activation route + a bind event),
  never a silent empty toolset or missing persona.
- **Run time:** the turn itself fails exactly as it does today.
- **Ownership time:** a non-token `owner` is a save-time `GraphError` (400,
  never written); a loop doc supplied to the loop door with the wrong
  `owner` is a 400 (never saved, the agent untouched); an owned loop never
  outlives its agent (the delete cascades it), and a shared loop never dies
  with a referencer — ownership is a lifecycle, enforced at the store.
- **Delete time:** deleting the bound agent is a VISIBLE rollback, not a
  silent one: every shared seam the bind mutated goes back to its pre-bind
  value (`bind_and_remember` captures the snapshot at bind;
  `detach_agent` / `restore_active_agent` restore it — loop slot +
  active graph, model + rebuild, MCP adapters + pool reset, settings,
  persona). The lazy per-turn hook covers every path that doesn't go
  through the delete route; a deleted agent never keeps running the
  next turn.
- **Deactivate time:** `POST /api/agents/deactivate` is the same VISIBLE
  rollback for a document the author keeps: `store.clear_active()` drops the
  pointer (persisted), `deactivate_agent` / `restore_active_agent` roll the
  seams back. It is the delete path's twin minus id-matching (deactivate
  undoes WHATEVER is bound; delete only undoes the bound agent that is being
  removed) and minus the file removal — the document stays saved. Idempotent
  (no bind state ⇒ no-op); a deactivated agent never keeps running the next
  turn, and the lazy per-turn hook self-heals any path that skipped it.

## Out of scope (named, NOT built)

- **Nested / routed agents** — the agent graph *sequencing or fanning out
  multiple sub-graphs* (preflight → research-sub → act-sub) with `route` as
  the inter-agent dispatcher, i.e. the original "agent groups — serial
  handoff" note. This needs a `subgraph` node **inside** the turn vocabulary
  plus the active-dataflow generalization. Larger contract. Do this only when
  "one loop per agent" stops being enough. **Deferred, not cancelled.**
- **Per-MCP / per-skill budgets** — extend the existing per-node/branch/graph
  ceilings later.
- **Secrets management** for MCP `headers`/env — trusted-dev assumption for
  now (matches the "trusted-dev auth boundary, not a security boundary"
  posture); allow-listing `mcp.command` is a named hardening, not built.
- **Live MCP result streaming to the browser** — backend-internal; the single
  `{answer}` seam stays.
- **Multiple simultaneously-active agents / routing to an agent** — that is
  the nested reading above.

## Tests (fakes only — repo frozen rule)

- **Validator:** no/2 `loop` rejected; unknown top-level key; `mcp` alias
  collision; wrong `transport` field pairing; `skill.path` missing; `settings`
  key not a slot; `model.provider` closed-enum.
- **Bind then run:** a fake MCP server (implements the harness `Tool` shape)
  is merged into the per-agent registry and **callable by the executor** in a
  normal `offline`-style turn; a fake skill's prompt fragment appears in the
  persona and its tool is registered; `settings.memory=trimmed` changes the
  snapshot.
- **Activation:** save-validates (invalid ⇒ 400); activate flips the active
  id; `describe()` carries the `agents` block; a mid-activation swap lands on
  the **next** turn (pinned per-turn-snapshot semantics).
- **Deactivate:** `POST /api/agents/deactivate` clears the active id but KEEPS
  the document (re-activatable afterwards); rolling back a bound agent restores
  EVERY shared seam (loop slot + active graph, model + rebuild, MCP adapters,
  settings aliases, persona) to the pre-bind pipeline — the same pin as the
  delete path, minus the file; idempotent no-op with nothing bound
  (`tests/test_agent_api.py` + `tests/test_agent_bind.py`).
- **Pool:** `get(alias)` reuses a live connection; `reset(alias)` re-spawns;
  no per-turn spawn (the fake connection counts its `connect` calls = 1 across
  N turns).
- **Owned loop (pinned by `tests/test_agent_loop_ownership.py`):** `owner`
  round-trips into the document and surfaces in `list_meta`; a malformed
  `owner` ⇒ `GraphError`, never written; `POST /api/agents/{id}/loop` builds
  the agent's private loop 1:1 (idempotent — the second call reuses the same
  ref), **stamps** `owner` even on replace, and attaches the agent's
  `loop.ref` to it (created together ⇒ no dangling ref); an invalid supplied
  graph is a 400 — not saved, not attached, the agent untouched; a loop with
  the wrong `owner` is a 400; `DELETE /api/agents/{id}` cascades the owned
  loop but **spares** a shared loop another agent still references;
  activation still resolves to `active_alias(LOOP) == "graph"` with the
  owned loop as `active_id` — bind-then-run, no new execution path.
  Designer side: `newAgentDraft(canvasGraphId)`, `graphToSaveBeforeAgent`,
  and the shared `LoopView` shape (`describeLoopCanvas` ≡
  `describeLoopGraph`) are pinned in `agentNode.test.mjs` /
  `agents.test.mjs` (fakes only).

## Build order

1. `schemas/agent.py` validator **first** (red → green, fakes), with the
   closed-vocab rules above. *(done — `6b35466`)*
2. `components/agent_store.py` + `server.py` `/api/agents` CRUD + activation
   + the `agents` block in `describe()`; fake tests (invalid ⇒ 400; activate
   flips active). *(done — `470cd0b`)*
3. `app/mcp/adapter.py` + `pool.py` with a **fake MCP server** (no real
   binary); pin "bind merges tools" and "no per-turn spawn". *(done —
   `6e32c74`)*
4. Bind path in `main.py::get_registry` / `agent.py`; `prompt`/`model`/
   `settings` feeding the context + snapshot; fake-driven "bind then run".
   *(done — `889cbe5`: `components/agent_bind.py` + `registry.rebuild()` +
   the activation route's bind-first 400 + the per-turn lazy hook in
   `build_agent()`)*
5. Skills: bind injects persona fragment + registers a fake tool. *(done —
   same commit; the skill fragment composes the persona and its declared
   tools must resolve at bind time)*
6. Config surface: ComponentPanel list/enable/**test** MCP; the designer's
   read-only Agents library with `loop` ref expandable. *(done — `84e29cc`
   the panel half incl. `POST /api/agents/{id}/mcp/test`; `AgentLibrary.tsx`
   + `lib/agents.ts` + `agents.test.mjs` the designer surface: one line per
   agent, the bundle's shape, the loop ref expandable with "open in canvas")*
7. Docs cross-refs + `app/memory/architecture.md` refinement. *(done — this
   status line, the README feature blurb + docs list, and the memory entry)*
8. The designer's **authoring surface** (items 1+2 of the continuation): the
   (continued —) **save-order**: `graphToSaveBeforeAgent` (pinned by
   `agentNode.test.mjs`) — when the loop slot names the canvas's own graph,
   `saveAgentNode` persists that canvas document first, so "save the loop
   before the agent" is no longer a manual step; see §"The agent node on the
   canvas", invariants;
   agent-node contract `lib/agentNode.ts` (draft ⇄ document, preflight mirror,
   the id rule) pinned by `agentNode.test.mjs` (fakes only); the canvas's
   agent layer (a separate selection kind, drag/select/delete) in
   `Canvas.tsx`; the on-node inspector `AgentCard`/`AgentPanel` (every field +
   Save/Activate/Delete-saved/Test-MCP over the REST door); the `page.tsx`
   wiring (add an agent node; open a saved agent from the library; rename;
   dirty = draft-vs-saved diff; the in-flight action per node); the
   "add agent" entry in the Palette and the library's "+ new" / "edit on
   canvas →". *(done — `d68b903`: `agentNode.ts` + tests, `AgentCard`/
   `AgentPanel`, the `Canvas` agent layer, the `page.tsx` wiring, the
   `Palette`/`AgentLibrary` entry points, the `api.ts` door client)*
9. **Owned loop — the node OWNS its loop's authoring** (the continuation):
   `newAgentDraft(canvasGraphId)` — a fresh node is BORN with its loop slot
   naming the canvas's own graph (the alias fallback only without a canvas
   id; pinned); `LoopPreview` — the card/panel SHOW the loop (LIVE from
   `describeLoopCanvas` when the ref is this canvas's graph, fetched via
   `describeLoopGraph` otherwise; both the one `LoopView` shape, pinned
   against each other; "open in canvas →" bridge); `LoopBuilder` — the
   inspector's INLINE builder (add/rename/remove nodes, add/remove wires,
   the repeat's `max_passes`, the verdict-only `⟲ repeat` rule) editing the
   canvas's shared working state through page.tsx's
   `renameLoopNode`/`removeLoopEdge` adapters (the builder is pure control
   surface — the save-order persists the loop doc first on Save); backend —
   `graph_store.owner_of/owned_by/delete_owned_by` + `owner` on
   `list_meta`, `POST /api/agents/{id}/loop` (the owned-loop door: seed or
   supplied loop, owner stamped, ref attached in one action, idempotent
   1:1), the `DELETE /api/agents/{id}` cascade, all pinned by
   `tests/test_agent_loop_ownership.py` (12 tests, fakes only); see
   §"The loop you own". *(done — this continuation of build order 8)*

## Why

The repo's pitch is orchestration-as-data, drawn visually, production risk
≈ 0. A specialist with its own model, MCP servers, skills, and flow is the
most recognizable "give this agent a *different* job" shape — and today it
means editing Python. The agent graph makes it a **validated, hot-swappable
document the designer renders** — "add an MCP / a skill" becomes *add a node,
re-save*; nothing re-routes silently; it fails at save time or as a visible
bind error. And because it's a configuration layer over the *existing*
slot/loop machinery, the pinned per-turn determinism and fake-test oracle stay
**exactly as they are**: the hot path is untouched.
