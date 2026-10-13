# 🧩 Loop Designer

The **Loop Designer** for [learning-ai-harness](../README.md): design agent-loop
orchestration as a JSON graph over the harness's component slots — the
ComfyUI-style second frontend promised by
[`docs/graph-loop-designer.md`](../docs/graph-loop-designer.md).

The backend is the single engine. This app is a canvas over its two doors:

- `GET /api/components` — the **one door**: live slots/presets **plus the
  closed 8-primitive vocabulary** the canvas can build with;
- `GET/POST/DELETE /api/graphs` (+ `{id}`, `{id}/last-run`) — graphs as
  JSON files, validated at save time (invalid ⇒ 400, never saved).
  `{id}/last-run` is the **canvas seam**: while a run is in flight it
  also serves the IN-FLIGHT record (`status: "running"` + events so far) —
  the designer polls it and highlights the currently executing node.

A **loop** is data, not code: `GraphLoop` (loop alias `graph`) executes the
active graph per chat turn. Activating
`{"loop": "graph", "graph_id": "..."}` takes effect on the **next**
`/api/chat` — the exact hot-swap model the rest of the harness uses.

## What it does

| Surface | Behavior |
|---------|----------|
| **Palette** | the 8 primitives (`prompt · research · plan · act · critic · gate · merge · parallel`) from the API's `primitives` block — the canvas renders exactly what the engine can run (offline, a frozen local table keeps it navigable) |
| **Canvas** | positioned node cards with typed ports; drag nodes, drag output→input to wire **data** edges, drag the dashed `⟲ repeat` port of a verdict node (`critic`/`gate`) for a **control** edge with a `max_passes` ceiling; pan (drag background), wheel/buttons zoom; click a wire to select, `Delete` to remove |
| **Inspector** | graph settings (name, budget ceiling, `max_parallel`); per-node config JSON, budget, `on_failure` (`abort`/`skip`/`retry(n)`); **parallel branches as inline JSON** (fresh-blackboard sub-graphs, `act` rejected inside by the validator); structural checks + the live document JSON |
| **Library** | the saved documents (`app/graphs/*.json`), active marker, load / duplicate (new id) / delete |
| **Run bench** | save (if dirty) → activate → one real `/api/chat` turn → answer + the node-event stream read back through `GET /api/graphs/{id}/last-run` (frozen v0 rule: real runs are evidence, never assertions). **Live progress:** while the turn is in flight the canvas + bench track the run — the executing node(s) pulse, the feeding wire animates, the bench lists `currently running: …`; when it ends, node states settle to `ok`/`failed`/`skipped` |

The shipped **killer workflow** (`app/graphs/killer-research.json`):
parallel research (web + local) → merge → act → critic with 2 bounded
retries.

## Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 15 (App Router) |
| UI | React 19 + TypeScript 5 (strict) + CSS Modules + design tokens shared with chat-ui (one light/dark preference) |
| Build | `standalone` output |
| Container | Docker (multi-stage, `node:20-alpine`) |

## Development

```bash
npm install
npm run dev        # http://localhost:3000; the dev server proxies /api/* and
                   # /health to BACKEND_PROXY_TARGET (default http://localhost:8000)
```

> The client uses **relative** URLs: it talks to the Next server, which
> proxies to the backend (`rewrites()` in `next.config.js`). That works from
> a browser on a *different machine* too — nothing like `localhost` is baked
> into the bundle. Set `BACKEND_PROXY_TARGET` (server-side env) to change the
> proxy target; keep `NEXT_PUBLIC_API_URL` only if you want a direct
> absolute-URL client (legacy behaviour).

## Production

```bash
npm run build
docker build -t loop-designer .
docker run -p 3020:3000 loop-designer
```

Or the compose stack: `docker compose up` (designer on :3020, chat on :3010,
API on :8000).

## Contract (frozen v0)

The canvas is a convenience surface; `app/schemas/graph.py` is the
authority. The two only diverge when a contributor adds a primitive to the
backend — and then the palette updates automatically, because it renders the
API, not a copy.
