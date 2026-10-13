/** @type {import('next').NextConfig} */
const nextConfig = {
  // Required by the two-stage production Dockerfile, which copies
  // `.next/standalone` (a self-contained `node server.js`) into the runner
  // image. Without this the standalone output is never produced and the
  // build stage's COPY fails.
  output: 'standalone',
  env: {
    // Unset (default) → the client uses relative URLs and this server
    // proxies to the backend (rewrites() below) — works from remote
    // browsers; setting NEXT_PUBLIC_API_URL keeps the legacy absolute-URL
    // behaviour.
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || '',
  },
  // Same-origin proxy (see next.config.js in designer/ for the rationale):
  // BACKEND_PROXY_TARGET is server-side only — the compose service name
  // inside Docker, `http://localhost:8000` for local dev.
  //
  // proxyTimeout: Next's rewrites proxy hard-aborts the upstream socket at
  // 30s of inactivity by default (see the longer rationale in
  // designer/next.config.js). Without this, any /api/chat turn longer than
  // ~30s of backend silence returns a bare 500 to the client even though
  // the run completes fine on the backend. 24h of tolerance.
  experimental: {
    proxyTimeout: 86_400_000,
  },
  async rewrites() {
    const base = process.env.BACKEND_PROXY_TARGET || 'http://localhost:8000';
    return [
      { source: '/api/:path*', destination: `${base}/api/:path*` },
      { source: '/health', destination: `${base}/health` },
    ];
  },
};

module.exports = nextConfig;
