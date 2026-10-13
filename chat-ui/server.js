/**
 * server.js — Custom server entry point for the chat-ui application.
 *
 * chat-ui is a Next.js 15 (App Router) app. `next.config.js` sets
 * `output: 'standalone'`, and the Dockerfile runs `node server.js` with
 * `PORT=3000` / `HOSTNAME=0.0.0.0`. This file is the canonical entry point
 * that boots the Next.js server and serves the chat-ui application.
 *
 * Conventions (matching the existing project):
 *   - CommonJS (same as next.config.js)
 *   - Only depends on `next` (already in package.json)
 *   - Respects PORT / HOSTNAME env vars (set by Dockerfile)
 *   - API proxying is done by next.config.js rewrites (same-origin for
 *     clients; BACKEND_PROXY_TARGET is the proxy target)
 *   - CORS is moot for proxied requests (the browser sees one origin)
 *   - Static assets (/_next/static, public/, /icon.svg) are served
 *     automatically by the Next.js request handler
 *
 * Usage:
 *   node server.js
 *
 * Environment:
 *   PORT      — port to listen on            (default: 3000)
 *   HOSTNAME  — interface to bind            (default: 0.0.0.0)
 *   NODE_ENV  — "production" for optimized serving, otherwise dev mode
 */

const path = require("path");
const http = require("http");
const next = require("next");

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// This file lives at the project root, so __dirname is the app directory.
const dir = __dirname;

// Match the Dockerfile defaults so `node server.js` behaves identically
// to the containerized entry point.
const port = parseInt(process.env.PORT, 10) || 3000;
const hostname = process.env.HOSTNAME || "0.0.0.0";

// In production, Next.js serves the pre-built app; in development it
// compiles on the fly with hot reload.
const dev = process.env.NODE_ENV !== "production";

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

const app = next({ dir, dev });
const handle = app.getRequestHandler();

async function start() {
  // app.prepare() must resolve before we start handling requests.
  await app.prepare();

  const server = http.createServer((req, res) => {
    // Every request (pages, static assets, /_next/*, /icon.svg, 404,
    // error boundaries) is delegated to the Next.js request handler.
    handle(req, res);
  });

  server.listen(port, hostname, () => {
    console.log(`> chat-ui ready  [${dev ? "development" : "production"}]`);
    console.log(`> Listening on   http://${hostname}:${port}`);
  });

  server.on("error", (err) => {
    if (err.code === "EADDRINUSE") {
      console.error(`> Port ${port} is already in use.`);
    } else {
      console.error("> Server error:", err.message);
    }
    process.exit(1);
  });

  // -----------------------------------------------------------------------
  // Graceful shutdown
  // -----------------------------------------------------------------------

  let shuttingDown = false;

  function shutdown(signal) {
    if (shuttingDown) return;
    shuttingDown = true;

    console.log(`\n> ${signal} received — shutting down gracefully…`);

    // Stop accepting new connections; let in-flight requests finish.
    server.close((err) => {
      if (err) {
        console.error("> Error during shutdown:", err.message);
      } else {
        console.log("> Server closed. Bye.");
      }
      process.exit(err ? 1 : 0);
    });

    // Safety net: force-exit if connections refuse to drain within 10 s.
    const forceTimer = setTimeout(() => {
      console.error("> Forced shutdown after timeout.");
      process.exit(1);
    }, 10_000);
    forceTimer.unref();
  }

  process.on("SIGINT", () => shutdown("SIGINT"));
  process.on("SIGTERM", () => shutdown("SIGTERM"));
}

start().catch((err) => {
  console.error("Fatal error starting chat-ui:", err);
  process.exit(1);
});
