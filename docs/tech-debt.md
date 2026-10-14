# Technical debt & tool enhancements

**Status:** living ledger — P1 items should be fixed before the tools/MCP
surface is considered trustworthy; P2/P3 are scoped enhancements.
**Date:** 2026-10-14.
Complements [agent-graph.md](agent-graph.md) (the MCP client and bind model it
points at) and the Dockerfile's empty-context guard (the host-permission trap
of 2026-10-14).

## How to use this file

Each item: what, where, impact, and the proposed move. When an item is
implemented, delete it (the code + tests are the record). Nothing here is
authoritative about the contract; the contract lives in the docs it links to.

## P1 — real failure modes, fix first

### 1. Stdio MCP: `stderr` is piped but never drained

`StdioConnection` spawns with `stderr=subprocess.PIPE` (app/mcp/client.py,
`connect()`) and only ever reads `stdout`. When the server writes more than
the OS pipe buffer (~64 KB) of stderr, its `write(2)` **blocks** and the
whole server hangs mid-turn — invisible, no timeout, no log. Same class of
bug as the deadlocked-subprocess pitfall in every stdlib subprocess tutorial.

**Fix:** a daemon drainer thread that reads stderr into a bounded ring
(deploy value: the last N lines for diagnostics), or `stderr=DEVNULL` for v1.
P1 because it is a *data-dependent* hang on a path this repo now depends on.

### 2. The stale-backend trap (incident: "the agent ignored my prompt")

The running image can lag the code: on 2026-10-14 the live `:8000` server
exposed only `/api/chat`, `/api/components*`, `/health` — no `/api/agents` —
so Activate 404'd (opaque "Not Found") while chat happily ran on the default
persona. The failure was *correct per build* and therefore invisible.

**Fix, in order:** a `GET /api/version` endpoint (monotonic constant in
`app/server.py`), the frontends probe it and show a loud "frontend expects
N, backend answers M — rebuild the app image" banner instead of generic
errors, and the `docker compose up` flow gets `--build app` in the
README/docs build steps. Cheap, and it converts the next occurrence of
this whole class from an afternoon into two minutes.

## P2 — v1 scope gaps (known, deliberate — don't let them rot into bugs)

### 3. MCP session lifecycle is half-talked

The client ignores the `mcp-session-id` response header from `initialize` and
sends none on later requests; `close()` only flips a flag (no `DELETE` with
the session header). Permissive servers (and our test fakes) don't care;
strict Streamable-HTTP servers/gateways may. Today's 406 was the Accept
header (fixed, see below) — a strict gateway's *next* likely complaint is
the session header. **Fix:** capture `mcp-session-id` in `_UrlConnection`
after `initialize`, echo it on subsequent requests, and issue a best-effort
`DELETE` in `close()`.

### 4. Stdio MCP inherits the FULL parent environment

`Popen(..., env=self._env)` with `env=None` means the spawned server sees
every credential the server process holds. The DSH harness scrubs
credential-shaped vars before spawning MCP servers for exactly this reason;
we don't. **Fix:** default to a small allowlist (PATH, HOME, LANG + the
document's `env`), never the full environment.

### 5. Protocol version is pinned, not negotiated

`PROTOCOL_VERSION = "2025-06-18"` is sent unconditionally; the
`protocolVersion` the server *offers* in the initialize result is dropped.
If a server requires a newer (or reports a different) version, follow it for
the session. **Fix:** read the result's `protocolVersion`, prefer the
server's supported version when it is ≥ ours.

### 6. One 30 s timeout to rule them all, no pool reaping

`DEFAULT_TIMEOUT` covers connect *and* body read *and* every tool call, and
pool entries live forever — a gateway that revokes a session makes
`tools/call` fail deep in a turn with no reconnect (the pool's "pooled,
never per-turn" rule is right; liveness just needs a re-earn path).
**Fix:** split connect/read/call timeouts; on a *connection-shaped* error
(reset, 401/403, 404 session) reset the pool entry and retry once.

### 7. `memory` tools are CWD-relative

`tools/memory.py` uses `Path("memory")` — behavior silently changes with the
process's working directory (REPL vs `python server.py` vs a container). The
skill-root anchor in `agent_bind._default_skill_root()` (file-derived, not
CWD-derived) is the model to copy. **Fix:** anchor `MEMORY_DIR` off the
app root, CWD as fallback.

### 8. Persona artifacts land in the repo tree — and so do root-only files

`_materialize_persona` writes `app/agents/<id>.persona.md` into the source
tree, and two incidents this week (designer build FATAL; a test file at
mode 600) show that files written by a root session come back unreadable to
the daemon. **Fix:** materialize personas under `app/data/` (gitignored,
like `python_workspace`), and add `find ... ! -perm -o+r` to the Dockerfile
guard *beside* the empty-file check — the guard caught the symptom; this
catches the permission class without needing a 0-byte file.

### 9. No real-transport MCP test

The pool/adapter suite is fakes-only (by the repo's rule) — but the *client*
itself (handshake framing, SSE extraction, the 406 regression) has zero
coverage against a real wire. **Fix:** a tiny in-repo stdio echo MCP server
(~80 lines, stdlib) as a test fixture: `initialize → tools/list → tools/call
→ close`, pinned by an integration test. Catches P2-3/5 regressions for free.

## Enhancement backlog (tools — `app/tools/`)

Ordered by leverage for the loop (the model's ability to actually finish
multi-step work):

1. **Windowed reads** (`summarize.py` `read_file`, `files.py`): add
   `offset`/`limit` with a "…(N more lines)" continuation marker instead of
   whole-file dumps that blow context on big files. The single highest-leverage
   tool change for research loops.
2. **Stateful Python session** (`python_tools.py`): a persistent REPL slot
   option (state carries across `run_python` calls in one turn) — today each
   call is a cold `python -c` and iterative debugging is impossible inside a
   graph budget. Keep the rlimit isolation; only add persistence.
3. **Search quality** (`search.py` `web_search`): `language`/`time_range`
   parameters passed to SearXNG, and distinct error text for "core down" vs
   "no results" (today both read like empty results to the model).
4. **PDF depth** (`pdf.py`): `read_pdf_pages(a, b)` range read, a
   metadata/outline tool, and an honest encrypted-PDF error (the model
   currently sees a traceback-shaped string).
5. **Memory structure** (`memory.py`): line-numbered search hits, a
   `remember(topic, ...)` that upserts into a section file under the tag
   (the `app/AGENTS.md` memory guidance already requires tags — the tool
   should make them one argument), and the CWD anchor fix (P2-7).
6. **Tool introspection door**: `GET /api/tools` (name/description/schema of
   the live `ToolRegistry`) so the designer can render the toolbelt an agent
   actually gets — currently only the MCP subset is inspectable.
7. **Ops niceties**: per-tool timeout metadata, a deprecation marker on
   `ToolRegistry.register`, and usage counters on the loop's executor to tell
   which tools earn their keep.

## Done in this pass (so the ledger starts honest)

- **Strict-gateway handshake** (app/mcp/client.py): `Accept: application/json,
  text/event-stream` on the URL transports (gateway.mcpservers.org answered
  `initialize` with 406 without it), and `_post` now branches on the
  response's **actual** `Content-Type` instead of the declared transport —
  the server chooses JSON vs SSE, per the spec. 40/40 `test_mcp.py` +
  `test_agent_api.py` green.
- **Host-permission incident** resolved: six root:root mode-600 files
  (designer Loop* components + one test + a valkey dump) normalized to
  1001:1001 / 644; the Dockerfile guard fired and did its job.
