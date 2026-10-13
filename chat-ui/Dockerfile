# syntax=docker/dockerfile:1

# =============================================================================
# chat-ui — Next.js 15 (App Router) frontend
#
# next.config.js sets `output: 'standalone'`, so `next build` produces a
# self-contained server at .next/standalone that runs without the full
# node_modules.  We exploit that for a small, fast image:
#
#   Stage 1 (build)  : install all deps, run `next build`
#   Stage 2 (runner) : copy the standalone server + static assets, run it
#
# Build:  docker build -t chat-ui .
# Run:    docker run -p 3000:3000 chat-ui
#
# IMPORTANT: The runtime CMD is `node server.js` (the standalone entry point).
# Do NOT override this with `next start` or `npm run start` in docker-compose
# or `docker run` — those require the full .next/ directory (including
# required-server-files.json) which is intentionally NOT copied into this
# image.  The standalone server is self-contained and never reads that file.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — build
# ---------------------------------------------------------------------------
FROM node:20-alpine AS build
WORKDIR /app

# NOTE: do NOT add a default ARG/ENV for NEXT_PUBLIC_API_URL here. NEXT_PUBLIC_*
# values are inlined at build time, and an image-level default would leak
# "localhost:8000" into every client bundle. The design is same-origin
# instead: the client uses relative URLs and rewrites() in next.config.js
# proxies /api/* + /health to BACKEND_PROXY_TARGET (a server-side runtime
# env, never baked into the JS). Pass --build-arg NEXT_PUBLIC_API_URL=...
# only if you deliberately want the legacy direct-absolute-URL client.

# Install ALL dependencies (dev deps are needed for the build: typescript, etc.)
# `npm ci` is deterministic (uses package-lock.json); fall back to `npm install`
# only if the lock file is absent.
COPY package.json package-lock.json* ./
RUN if [ -f package-lock.json ]; then \
        npm ci --no-audit --no-fund; \
    else \
        npm install --no-audit --no-fund; \
    fi

# Copy the application source (node_modules and .next are excluded by
# .dockerignore, so this is safe and fast).
COPY . .

# Guard: fail loudly if ANY source file arrived EMPTY in the build context.
# Root-owned / 0600 files on the host can be turned into 0-byte placeholders
# by the COPY archiver, which previously produced the confusing
# TS2306 "File ... is not a module" type-check error deep inside `next build`.
# Fix on the host (sweep the whole tree, not just the one failing file):
#   find . -path ./node_modules -prune -o -type f -user root -print0 \
#     | xargs -0 -r sudo chown <your-user>:<your-group>
#   find . -path ./node_modules -prune -o -type f -user root -print0 \
#     | xargs -0 -r sudo chmod 644
RUN if find app -type f -empty | grep -q .; then \
        echo "FATAL: empty source files in build context (host permission issue?):"; \
        find app -type f -empty; \
        exit 1; \
    fi

# Run the production build.  With output:'standalone' in next.config.js this
# emits:
#   .next/standalone/          ← self-contained server (server.js + minimal node_modules)
#   .next/static/              ← client-side JS/CSS bundles
#   .next/server/              ← compiled server chunks (referenced by standalone)
#
# NOTE: required-server-files.json is produced in .next/ but is NOT part of
# the standalone output.  It is only needed by `next start`, which we do NOT
# use at runtime.
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — runner (minimal, non-root)
# ---------------------------------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# Run as the unprivileged user that ships with the node image
USER node

# --- Copy the standalone server -------------------------------------------
# .next/standalone/ contains:
#   server.js                  ← the entry point (self-contained Express-like server)
#   node_modules/              ← minimal runtime deps (next, react, react-dom, …)
#   .next/server/              ← compiled server chunks
#   package.json               ← {"type":"commonjs"}
#
# This COPY places server.js at /app/server.js and node_modules at
# /app/node_modules — exactly where `node server.js` expects them.
COPY --from=build /app/.next/standalone ./

# --- Copy static assets (NOT included in the standalone output) ------------
# Client-side JS bundles, CSS, images referenced by the app.
COPY --from=build /app/.next/static ./.next/static

# --- Copy the public directory (optional; harmless if empty) ---------------
COPY --from=build /app/public ./public

EXPOSE 3000

# Healthcheck: verify the server responds on port 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3000/ || exit 1

# --- Entrypoint / Command ---------------------------------------------------
# The standalone server.js is a self-contained HTTP server.  It does NOT
# read .next/required-server-files.json (that file is only consumed by the
# `next start` CLI, which we do not use).
#
# Do NOT change this to:
#   CMD ["next", "start"]          ← will ENOENT on required-server-files.json
#   CMD ["npm", "run", "start"]    ← same problem
#   CMD ["next", "dev"]            ← dev server, wrong for production
#
# If you ever need to switch to `next start`, you must also change the COPY
# instructions above to copy the FULL .next/ directory and node_modules:
#   COPY --from=build /app/.next ./
#   COPY --from=build /app/node_modules ./
#   COPY --from=build /app/package.json ./
#   CMD ["npx", "next", "start", "-p", "3000"]
#
# But that produces a much larger image and defeats the purpose of
# output:'standalone'.

CMD ["node", "server.js"]
