# The Stateless Agent Pipeline (v2 contract)

The agent pipeline is **request-scoped configuration**. The full capability
document — persona, model, MCP servers, skills, slot settings, and the loop —
rides in the `POST /api/chat` body. The server stores **no configuration**:
an absent or partially-specified `agent` field falls back to process
defaults, exactly as the unbound pipeline does today.

This supersedes the stored-activation model (the `agents/` store,
`/api/agents` CRUD + activate, materialized `<id>.persona.md`, `loop.ref`),
which is retired in Phase 3 of the stateless-pipeline migration
(see `memory/architecture.md`). The design invariant of the previous
contract — closed vocabulary, validate at the edge, bind-then-run, visible
failure — is preserved; only *where the document lives* changes:
**the client is the store**, the same way the client already owns the
conversation thread.

## 1. The request body (pinned shape)

```jsonc
POST /api/chat
{
  // required / existing (unchanged)
  "message": "one user message (1..10_000 chars)",
  "conversation": [                      // optional; client-owned thread, oldest first
    { "role": "user" | "agent", "content": "…" }
  ],

  // optional — the FULL configuration for this turn; absent = defaults
  "agent": {
    "id":     "client-bookkeeping-token",   // optional, shape-validated, ignored semantically
    "name":   "Client display name",        // optional, shape-validated, ignored semantically

    // persona — see §3
    "prompt": "Answer like a 5th-grade explainer.",

    // skills — persona fragments + declared bundled tools; see §4
    "skill": [
      { "text": "Prefer small, verifiable steps…", "tools": [] },
      { "path": "skills/research.md",             // server-side shared skill (the explicit shared case)
        "tools": ["search_memory", "read_memory"] }
    ],

    // model — see §5
    "model": { "provider": "ollama", "id": "Duggles/qwen-3-8-larger-context:latest", "params": {} },

    // MCP servers — see §6
    "mcp": [
      { "alias": "echo", "transport": "sse", "url": "http://…", "headers": {} }
    ],

    // slot overrides — existing vocabulary (slot name from components/slots.py -> alias)
    "settings": { "memory": "trimmed" },

    // the loop — exactly one of (see §2)
    "loop": { "alias": "plan_execute" }
    // | { "graph": { /* a v0/v1 graph document, validated by validate_graph */ } }
  }
}
```

Response (unchanged): `{ "answer": "…" }`.
Errors: `400` with the validator's path-qualified message
(`AgentError` / `GraphError`) — the same visible-failure posture as the
old save edge. An invalid document never runs a half-bound pipeline.

## 2. The loop (`agent.loop`)

Exactly one of:

- **`{ "alias": "plan_execute" | "direct" }`** — a built-in loop. Closed
  enum (`KNOWN_LOOP_ALIASES`), unchanged.
- **`{ "graph": { … } }`** — an **inline** graph document, validated by the
  same pure `validate_graph` contract as today (`schemas/graph.py`):
  closed primitive vocabulary, typed edges, one entry node, bounded repeats,
  `parallel` inline branches, optional budgets. The graph document is
  **self-contained in the body** — no server-side lookup.

`loop.ref` (a saved graph id) is a **transitional form only** (Phase 1,
while the design canvas still resolves saved docs). It does not appear in
this contract; it is deleted with the graph store in Phase 3. The saved-loop
canvas therefore becomes **client-side**: the canvas owns its documents and
sends the one in use as `loop.graph`. A standalone graph run is an `agent`
body with `loop.graph` and nothing else — the same door, no special endpoint.

## 3. The persona

- `prompt` is the persona text. Present → it **replaces** the process
  default (`AGENTS.md`, the `agent_path` default) for this turn — the
  replace-not-extend rule of the old bind, unchanged.
- Persona is **composed, then injected**: `prompt` + `skill[].text`
  fragments in declared order (the old bind's composition order).
- **No file is written.** The composed text lands in the request-scoped
  runtime as the conversation's leading `SystemMessage`. There is no
  `agent_path` indirection, no `<id>.persona.md`, no materialization.
- `prompt` absent + skills present → the default base (`AGENTS.md`) is
  composed with the fragments, as before. `prompt` absent + no skills →
  the default persona stands.
- The persona rides `messages[0]` of every Ollama call (API-inherent;
  see the send-path audit in `memory/architecture.md`). This design
  changes nothing on the wire; it removes the stored copy that
  shadowed it.

## 4. Skills (`agent.skill[]`)

Array of entries, each **exactly one** of `text` (inline fragment) XOR
`path` (a readable text file under the server skill root — the explicit
*shared* case: the one config surface that lives server-side by design),
with optional `tools` (declared bundled tool names, must be registered —
visible failure, as today). Order is preserved for composition.

## 5. Model

Closed v1 enum: `provider: "ollama"`, `id`, optional `params`. Absent →
the process default model (`inference/config.py`). One provider is
constructed **per request** against the request-scoped context; there is
no shared `context.llm` to swap.

## 6. MCP

Entries as today (`alias`, `transport` ∈ stdio|sse|http, `command`/`url`,
optional `headers`); aliases unique within the document. The **connection
pool stays a process-shared infrastructure cache** (reusing live sockets
across requests is not "storing configuration" — it holds connections,
keyed so concurrent requests with the same alias+spec share, different
specs never collide). Tool adapters are registered on the
**request-scoped** tool registry, so two concurrent pipelines with the same
alias but different specs cannot clobber each other's tools.

## 7. Defaults (no `agent` field; or field absent within it)

| Field    | Default when absent                                     |
|----------|---------------------------------------------------------|
| persona  | `AGENTS.md` (process default)                           |
| model    | `DEFAULT_MODEL` (`inference/config.py`)                 |
| loop     | `plan_execute` (the default preset's loop alias)        |
| mcp      | none                                                    |
| skill    | none                                                    |
| settings | the built-in active aliases                             |

This is **byte-for-byte the pipeline the server runs today** for an
unbound turn — the no-config path is deliberately unchanged, which is what
makes the fallback free.

## 8. Concurrency and isolation

- One request = one `ComponentContext` + registry snapshot, built for the
  turn and discarded after it. **No process-global active agent exists**,
  so the old cross-client clobbering mode (activating in one client
  silently changing the next turn of any client) is structurally gone.
- Shared across requests: the component *library* (slot/alias factories —
  code, not config), the MCP connection pool, `memory/` (agent-managed
  data, not config), `python_workspace` (outputs), and the startup defaults
  (`AGENTS.md`, `DEFAULT_MODEL` — build-time constants).
- Nothing in `§1` is written to disk by the server.

## 9. What this removes (Phase 3)

- `components/agent_store.py`, `agents/*.json`, `agents/_meta.json`
- `/api/agents` CRUD, `/api/agents/{id}/activate`, `/{id}/mcp/test`
  (replaced by a body-scoped test: same alias spec in the body), owned-loop
  cascade (`owner` field, `delete_owned_by`)
- `_materialize_persona`, `_EXTRAS_PERSONA_PATH`, `prev_agent_path`
  restore, `ensure_agent_bound` + `_EXTRAS_BIND_STATE` signature bookkeeping
- `loop.ref` as a contract path (transitional in Phase 1)
- `agents/agent1.json`, `agents/agent1.persona.md`

Surviving: `bind_agent()` as the storage-blind, per-request bind function
(persona target switches from file to runtime extras), the closed-vocabulary
validators, the six-slot model, and the graph loop engine.

## 10. Build order

1. **Contract** — validator: `loop.graph` inline, `skill.text` XOR `path`;
   `ChatRequest.agent`; body-shape contract tests (pinned first).
2. **Accept** — `run_turn(doc, message, history)`: request-scoped context,
   bind (persona → extras text), run, discard; absent `agent` = §7
   defaults, unchanged. Concurrency regression test included.
3. **Move** — chat-ui + designer carry the document client-side and send it
   each turn; "Activate/Save" becomes a client preference; `mcp/test`
   body-scoped; canvas loops sent as `loop.graph`.
4. **Remove** — §9 deletions; this doc stays the authority;
   `memory/architecture.md` carries the decision trail.
