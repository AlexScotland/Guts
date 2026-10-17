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
 */
const PAGES_EXPORT = process.env.PAGES_EXPORT === "1";
// The Pages site publishes the designer under /designer/ (landing page owns
// the root). basePath bakes that prefix into asset/root URLs; it is NOT part
// of the emitted file layout, so `out/` still lands its assets in _next/.
const PAGES_BASE = process.env.PAGES_BASE_PATH || "/designer";

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: PAGES_EXPORT ? "export" : "standalone",
  ...(PAGES_EXPORT ? { basePath: PAGES_BASE } : {}),
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
    return [
      { source: '/api/:path*', destination: `${base}/api/:path*` },
      { source: '/health', destination: `${base}/health` },
    ];
  },
};

module.exports = nextConfig;
