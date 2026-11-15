/**
 * Build modes:
 *  - DEFAULT (Dockerfile / dev server): output 'standalone' + the same-
 *    origin proxy below. Required by the two-stage production Dockerfile,
 *    which copies `.next/standalone` (a self-contained `node server.js`)
 *    into the runner image — without this the standalone output is never
 *    produced and the build stage's COPY fails.
 *  - PAGES_EXPORT=1 (GitHub Pages — docs/deploy-demo.md): output 'export'
 *    — a fully static site the Pages workflow publishes under /designer/.
 *    Static export does not support rewrites, so the client talks straight
 *    to the live backend via NEXT_PUBLIC_API_URL (cross-origin; CORS is
 *    already open on the API).
 *  - SUBPATH (reverse proxy under /designer, e.g. aifactory's Tailscale
 *    serve that STRIPS the prefix in transit): dev server with
 *    NEXT_PUBLIC_BASE_PATH=/designer → assetPrefix mode; the app serves at
 *    / while assets, the HMR websocket and API calls all stay under the
 *    prefix. See the SUBPATH block below.
 */
const PAGES_EXPORT = process.env.PAGES_EXPORT === "1";
// The Pages site publishes the designer under /designer/ (landing page owns
// the root). basePath bakes that prefix into asset/root URLs; it is NOT part
// of the emitted file layout, so `out/` still lands its assets in _next/.
const PAGES_BASE = process.env.PAGES_BASE_PATH || "/designer";
// Serving the designer under a reverse-proxy SUBPATH (aifactory's
// `tailscale serve` maps /designer → this app, every other prefix to other
// services, and STRIPS the mapped prefix in transit — proven by the dev
// server's own log: a browser request to ts.net /designer/api/components
// arrives here as GET /api/components). So the app must SERVE at / while
// every URL the browser sees carries the prefix so the proxy routes it back
// after stripping:
//   page   /designer/  → arrives /          → app route              ✓
//   assets /designer/_next/* → arrives /_next/* → dev assets         ✓
//   HMR    /designer/_next/webpack-hmr (get-socket-url builds this FROM
//          assetPrefix) → direct/LAN: 101 upgraded (verified). Via the
//          stripping proxy it arrives as /_next/webpack-hmr and the dev
//          server rejects the upgrade, so there is NO live-reload through
//          ts.net — hard refresh (⌘⇧R) works and is the only caveat.
//   api    /designer/api/* → arrives /api/* → rewrites → backend     ✓
// assetPrefix is the server-side lever (asset + socket URLs);
// NEXT_PUBLIC_BASE_PATH is the client-side one (app/lib/api.ts prefixes
// API calls with it) — one env, one prefix, both sides.
// Direct/LAN access (no proxy in front) is equally first-class: the UI's
// prefixed requests (/designer/api/*, /designer/health) are proxied by
// extra rewrites, and …/designer redirects to / — served by
// rewrites()/redirects() below. Those rules can only match direct traffic
// (the proxy strips the prefix first), so the two contexts share one build.
// NOTE: `basePath` alone does NOT work here — it moves routes to
// /designer, but serve delivers stripped paths (the log above), so the
// page 404s. (For a full-path proxy, basePath would be the right tool.)
const SUBPATH = process.env.NEXT_PUBLIC_BASE_PATH || undefined;

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: PAGES_EXPORT ? "export" : "standalone",
  ...(PAGES_EXPORT
    ? { basePath: PAGES_BASE }
    : SUBPATH
      ? { assetPrefix: SUBPATH }
      : {}),
  // Top-level (NOT experimental) in Next 15.5: allow the HMR websocket from
  // origins served through a reverse proxy (e.g. the Tailscale serve
  // hostname), in addition to localhost, where the dev server is normally
  // used.
  ...(process.env.NEXT_PUBLIC_DEV_ORIGIN
    ? { allowedDevOrigins: ["localhost", process.env.NEXT_PUBLIC_DEV_ORIGIN] }
    : {}),
  env: {
    // If NEXT_PUBLIC_API_URL is unset (the default), the client uses
    // *relative* URLs and talks to this server, which proxies to the
    // backend — see rewrites() below. That works from any browser (local or
    // remote) and no "localhost" is ever baked into the JS bundle. If you
    // set it, that absolute URL is used verbatim (legacy behaviour).
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || '',
  },
  // Same-origin proxy: the browser hits this Next server with relative
  // paths; it forwards to the backend. BACKEND_PROXY_TARGET is a
  // server-side env (read by the Next server, never inlined into the
  // client bundle): the compose service name (`http://app:8000`) inside
  // Docker, `http://localhost:8000` for local dev.
  //
  // proxyTimeout: Next's rewrites proxy hard-aborts the upstream socket at
  // 30s of inactivity by default (router-utils/proxy-request.js:
  // `proxyTimeout || 30000`, then http-proxy `v.setTimeout(..., abort)`).
  // A killer /api/chat turn is ONE long idle gap — the backend sends no
  // bytes until the whole graph run finishes (30s–10min on the 27B
  // model) — so any turn past ~30s of backend silence 500s with a bare
  // `Internal Server Error` while the run itself completes `ok` (see
  // _probe_proxytimeout/ A/B/C proof: default → 500@30.0s, this value →
  // 200@40.0s; `null` does NOT disable it — it falls back to 30000).
  experimental: {
    proxyTimeout: 86_400_000, // 24h of tolerance; a turn longer than that fails at the budgets, not here
  },
  async rewrites() {
    // Static export (Pages) ignores rewrites; the client uses the absolute
    // NEXT_PUBLIC_API_URL directly. Guard here so a PAGES_EXPORT build never
    // emits proxy rules that can't work in a static context.
    if (PAGES_EXPORT) return [];
    const base = process.env.BACKEND_PROXY_TARGET || 'http://localhost:8000';
    const rules = [
      { source: '/api/:path*', destination: `${base}/api/:path*` },
      { source: '/health', destination: `${base}/health` },
    ];
    if (SUBPATH) {
      // Direct-access shims: the UI (app/lib/api.ts) and any in-network
      // caller always request the prefixed form (/designer/api/*,
      // /designer/health). Through the stripping tailnet proxy those are
      // already de-prefixed before they reach us (and hit the rules above);
      // over the LAN / direct, they arrive prefixed and would 404, because
      // assetPrefix keeps the *routes* at the root. So proxy the prefixed
      // shape explicitly. This restores pre-assetPrefix direct behaviour.
      rules.push(
        { source: `${SUBPATH}/api/:path*`, destination: `${base}/api/:path*` },
        { source: `${SUBPATH}/health`, destination: `${base}/health` },
      );
    }
    return rules;
  },
  redirects() {
    // The page now serves at / (stripping-proxy model); anyone who still
    // dials the old direct URL …/designer gets bounced to it. Through the
    // proxy the request is stripped to / before it reaches us, so this only
    // ever fires for direct/LAN hits.
    return SUBPATH ? [{ source: SUBPATH, destination: "/", permanent: false }] : [];
  },
};

module.exports = nextConfig;
