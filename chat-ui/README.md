# 🤖 Learning Harness — Chat

A fast chat interface for the learning-ai-harness AI assistant. Built with **Next.js 15** (App Router), **React 19**, **TypeScript**, and **CSS Modules** with a **light/dark theme** design system.

## Features

- **Stateless backend contract** — the thread lives in the browser (`localStorage`) and is sent with every request, so a backend restart never loses the conversation.
- **Connection indicator** — the header polls `GET /health` and shows online / offline / checking.
- **Retry & stop** — failed sends surface in a retry bar (retry the last message or dismiss); in-flight sends can be aborted from the composer.
- **Theme** — light/dark toggle, persisted, applied before first paint (no flash), respects `prefers-color-scheme`.
- **Markdown rendering** — agent replies render as GitHub-flavored Markdown with copy buttons.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 15 (App Router) |
| UI | React 19 + CSS Modules + `react-markdown` / `remark-gfm` |
| Theme | Light/dark (design tokens in `globals.css`, `data-theme` on `<html>`) |
| Language | TypeScript 5 (strict) |
| Build output | `standalone` (self-contained `server.js`) |
| Container | Docker (multi-stage, `node:20-alpine`) |

## Getting Started

### Prerequisites

- Node.js 20+
- npm 10+

### Install & Run (development)

```bash
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

### Production Build

```bash
npm run build
node .next/standalone/server.js   # or: docker run (below)
```

### Docker

```bash
# Build
docker build -t chat-ui .

# Run
docker run -p 3000:3000 chat-ui
```

> The image runs the **standalone** server (`node server.js`). Do not swap in
> `next start` / `npm run start` without also copying the full `.next/`
> directory — see the Dockerfile notes.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND_PROXY_TARGET` | `http://localhost:8000` | Target of this server's same-origin API proxy (`rewrites()` in `next.config.js`; read by the Next server only). In Docker use the compose service name, e.g. `http://app:8000`. |
| `NEXT_PUBLIC_API_URL` | *(unset → relative)* | Legacy: absolute backend base URL, inlined into the client bundle at build time. Unset by default so the client stays same-origin and works from remote browsers. |

Copy `.env.example` to `.env.local` and adjust as needed:

```bash
cp .env.example .env.local
```

## Scripts

| Command | Description |
|---------|-------------|
| `npm run dev` | Start the development server with hot reload |
| `npm run build` | Create the production build (standalone output) |
| `npm run lint` | Run ESLint |

## Project Structure

```
├── app/                      # Next.js App Router
│   ├── layout.tsx            # Root layout (metadata, pre-paint theme script)
│   ├── page.tsx              # Chat page (composes the components below)
│   ├── not-found.tsx         # 404 page
│   ├── error.tsx             # Error boundary
│   ├── global-error.tsx      # Root error boundary
│   ├── loading.tsx           # Loading state
│   ├── icon.svg              # Favicon
│   ├── globals.css           # Design tokens + global styles
│   ├── components/
│   │   ├── ChatHeader.tsx    # Title bar: status dot, new chat, theme toggle
│   │   ├── MessageList.tsx   # Message bubbles, markdown, typing indicator
│   │   ├── ChatInput.tsx     # Composer with send / stop button
│   │   └── RetryBar.tsx      # Failure banner with retry / dismiss
│   ├── hooks/
│   │   ├── useChat.ts        # Chat state + API communication
│   │   └── useTheme.ts       # Light/dark theme, persisted
│   └── lib/
│       ├── api.ts            # Backend client (chat + health)
│       ├── thread.ts         # localStorage thread persistence
│       └── types.ts          # Shared types + backend caps
├── next.config.js            # Next.js config (standalone output)
├── tsconfig.json             # TypeScript config
├── package.json              # Dependencies + scripts
├── Dockerfile                # Multi-stage Docker build
└── .dockerignore             # Docker build context exclusions
```

## API

The chat UI talks to a **stateless** backend. The thread is owned by the
client and sent in full with every message.

### Send a turn

```
POST {backend base}/api/chat      # same-origin via /api/chat through chat-ui
Content-Type: application/json

{
  "message": "Hello!",
  "conversation": [
    { "role": "user",  "content": "…" },
    { "role": "agent", "content": "…" }
  ]
}
```

- `conversation` is the prior thread, **oldest first**. An empty array is a
  fresh conversation. Roles are `user` / `agent`.
- Caps: message ≤ 10,000 chars, each stored turn ≤ 50,000 chars (enforced by the backend).

Response:

```json
{ "answer": "Hi there! How can I help?" }
```

### Health check

```
GET {backend base}/health      # same-origin via /health through chat-ui
```

```json
{ "status": "ok" }
```
