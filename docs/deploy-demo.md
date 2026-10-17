# Public demo — GitHub Pages (+ optional live backend)

Status: live posture frozen — one page, one replay, one rule (the determinism contract)
Date: 2026-10-17
Supersedes: — (complements [tutorial.md](tutorial.md) §9 and the `designer/` surfaces)

## What a stranger sees

The Pages site is served at the **apex of a host** (a user/organization site
or a custom domain — the landing's links are absolute, e.g. `/designer/`, and
the designer's `basePath` is baked as `/designer`). At the root, a fully
static landing page:

1. **Replay of a real run** — the `sample-manual-route` graph (a route node with
   the `manual` decider pinned to the `notes` branch) rendered as an event
   driver: the node-event stream of a *real* captured run (`7 events`,
   `status ok`) played back with human pacing, mirroring node-for-node the
   canvas logic in `designer/app/lib/run.ts`. The footer states honestly that
   the run made **zero LLM calls**.
2. **Try it live** — the Loop Designer, statically exported to `/designer/`.
   With a live backend baked in (below) it talks to it directly; without one,
   it is a read-only look at the product (see *Caveats*).

The landing and replay need **no backend at all** — they are bytes on GitHub
Pages. The backend, when present, is only ever the thing `/designer/`
talks to and the thing the *live* replay could be captured from.

## Why "replay of a real run" is honest — the determinism contract

The replay fixture (`deploy/pages/assets/replay/run.json`) is not a mockup:
it is a real run against the real engine, captured with the repo's own
`offline` preset posture (tutorial §9):

- the engine's components are the LLM-free ones (`passthrough` goal,
  `trivial` plan, `echo` executor, `always_complete` evaluator), and
- the graph's route node uses the `manual` decider with `pinned: notes` and
  `budget: 0` — the decision is made *in the document*, not by a model.

That combination makes an entire turn **deterministic**: two consecutive
runs, fresh conversations, produce bit-identical semantic event streams
(timestamps aside). The contract is enforced, not assumed:

- `deploy/tools/capture_demo_run.py` runs the turn twice and **aborts**
  (exit `2`) if the two streams differ — the fixture is never written from
  a non-deterministic run.
- `deploy/tools/pages_smoke.mjs` imports the real player with a minimal DOM
  shim and asserts the product-visible result (event states, lit/untaken
  edges, answer revealed, honest real-duration note). CI-safe, zero deps.

So the player's "replay" is exactly what *any* visitor's run would be —
yours, the live instance's, or the capture's.

## Pieces, and where they live

| Piece                            | Source of truth                                | Needs backend? |
|----------------------------------|------------------------------------------------|----------------|
| Landing + replay player          | `deploy/pages/` (HTML/CSS/JS, committed)       | no             |
| Replay fixture                   | `deploy/pages/assets/replay/run.json`          | no             |
| Live API banner (optional)       | `deploy/pages/config.json` (`live_api`)        | no             |
| Loop Designer (static)           | built by CI from `designer/` into `deploy/pages/designer/` (gitignored; [next.config.js](../designer/next.config.js) `PAGES_EXPORT` mode) | optional |
| Live demo backend (optional)     | the real `app/` with `GUTS_DEMO=1` (see below) | it is the backend |

## The live demo backend (optional, one env var)

The shared public instance (e.g. a free Render service) is the *real* app in
a stranger-safe posture. One environment variable — `GUTS_DEMO=1` — is all
that changes:

```
GUTS_DEMO=1 uvicorn server:app --host 0.0.0.0 --port $PORT
```

On startup, [`app/demo_bootstrap.py`](../app/demo_bootstrap.py)
(`bootstrap_demo()`, wired in `app/server.py`):

1. activates the **`offline` preset** (zero LLM, deterministic), and
2. makes the **`sample-manual-route`** graph the active loop graph,

so the Loop Designer opens with something interesting already drawn and a
clicking stranger can "try it" without burning model budget. The call is
idempotent and fails soft — a broken seed logs a warning and never kills
boot. With `GUTS_DEMO` unset, nothing in this file runs.

Because the `offline` preset is deterministic (the contract above), the live
instance's runs are *identical* to the Pages replay — the demo is the
product, not a stand-in.

## Refreshing each piece

**Replay fixture** (re-capture from a live backend):

```bash
python3 deploy/tools/capture_demo_run.py \
    --base http://127.0.0.1:8909 \
    --out  deploy/pages/assets/replay/run.json
```

**Loop Designer static build** (locally, e.g. to inspect the `out/`):

```bash
cd designer && npm ci && PAGES_EXPORT=1 next build   # → designer/out/
cp -R designer/out/. ../deploy/pages/designer/       # land it where CI would
node ../deploy/tools/pages_smoke.mjs                 # (smoke still passes)
```

**Live API banner** — set the Pages-site `live_api` in `deploy/pages/config.json`
to the HTTPS origin of the backend (the player only shows it when non-empty;
the designer build bakes the same origin in as `NEXT_PUBLIC_API_URL`, see
below). Both are optional: blank is a valid, backend-free site.

## CI — `.github/workflows/pages.yaml`

One workflow, three gates, `pages:write` only for the final deploy job
(modern `actions/deploy-pages` + `upload-pages-artifact` shape):

1. **backend suite** — `pytest` on the real `app/` (the oracle);
2. **designer build** — `PAGES_EXPORT=1 next build`, then copy `out/` into
   `deploy/pages/designer/` (that directory is gitignored — the *artifact*
   is the source of truth, not a committed copy);
3. **smoke** — `node deploy/tools/pages_smoke.mjs` (the landing + player
   contract, with the real fixture).

Only after all three pass does the `deploy` job publish `deploy/pages` to
the configured `github-pages` environment. `NEXT_PUBLIC_API_URL` is read from
the repo's **`LIVE_API_URL`** variable (blank → the designer is same-origin,
which is what makes the static build honest without a backend).

## Local preview

The whole site is static; no backend needed:

```bash
python3 -m http.server 8080 --directory deploy/pages
# → http://localhost:8080/          landing + replay
# → http://localhost:8080/designer/ designer (needs a live API to be useful)
node deploy/tools/pages_smoke.mjs   # headless contract check
```

## Caveats — the shared-instance semantics

- **One process, one registry.** The live demo backend is a single Python
  process; activating a preset or graph mutates the *process-global*
  component registry. `GUTS_DEMO` instances are, by design, a *shared
  playground*: a stranger who flips the preset changes it for everyone on
  that instance. (This is why the demo posture is the *deterministic* one —
  whatever anyone runs, the run is still real and the replay still matches.)
- **In-memory last-runs clear on every deploy** — the "fresh demo" posture a
  public instance wants. Everything else (graphs, presets, the fixture) is
  committed and restart-stable.
- **Static designer without a backend** can render and be browsed, but its
  live-run polling will 404 (relative `/api/*` on a static host). That is the
  honest state: the *replay* needs no backend, the *live* designer does.
- **`critic`/`gate` graphs fail-fast under `offline`** (they need a model).
  The pinned showcase (`sample-manual-route`) is safe by construction — that
  is exactly why it is the demo graph.
