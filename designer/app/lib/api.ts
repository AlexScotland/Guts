/**
 * API client for the GUTS backend (designer surface).
 *
 * Endpoints (see app/server.py):
 *   GET    /api/components             — one door: slots, presets, primitives, graphs
 *   GET    /api/graphs                 — saved documents (active + list)
 *   POST   /api/graphs                 — save (validated; invalid = 400, never saved)
 *   DELETE /api/graphs/{id}            — delete a document
 *   GET    /api/graphs/{id}/last-run   — last run record: status, answer, node events
 *   POST   /api/components/activate    — {"loop": "graph", "graph_id": "..."}
 *   POST   /api/chat                   — one turn (stateless: full thread rides each call)
 *   GET    /health                     — liveness probe
 */

// `import type` (erased by Node's type stripping) — api.ts must not resolve
// ./types at runtime under the .mjs test runner (extensionless specifiers
// don't resolve there).
import type {
  AgentDetail,
  AgentDocument,
  AgentsStatus,
  ComponentsStatus,
  GraphDoc,
  GraphsStatus,
  LastRun,
} from "./types";

/** Backend/client error, tagged by failure world so the UI can phrase it:
 *  "network" = the request never landed (service down, DNS, CORS);
 *  "http" = it landed and the other side refused (status / JSON-RPC error);
 *  "parse" = an answer landed but was not the shape we contract for.
 *
 * Defined HERE (not in types.ts) on purpose: this is the one module with a
 * RUNTIME import of it, and Node's type-stripping test runner only resolves
 * explicit-extension specifiers — keeping the runtime surface inside this
 * file is what lets the pure type file stay import-erased.
 *
 * Re-exported for convenience: client code that catches backend errors
 * (e.g. the live-run poller, which must ignore 404s) imports it from here. */
export class ApiError extends Error {
  kind: "network" | "http" | "parse";
  status?: number;
  constructor(kind: "network" | "http" | "parse", message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = status;
  }
}

/** Backend base URL.
 *
 * Default (empty) → relative URLs: requests are same-origin and the Next
 * server proxies them to the backend (rewrites() in next.config.js). That
 * works even when the browser is on a *different machine* than the backend —
 * never bake "localhost" into the client.
 *
 * When the designer is served under a reverse-proxy subpath (e.g. Tailscale
 * serve mapping `/designer` to it), NEXT_PUBLIC_BASE_PATH carries that
 * prefix so API calls stay inside it and reach the designer's own proxy —
 * a browser cannot send absolute `/api/...` across a path-splitting proxy.
 *
 * Set NEXT_PUBLIC_API_URL to an absolute URL to talk to the backend
 * directly (legacy behaviour; wins over both). */
export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || process.env.NEXT_PUBLIC_BASE_PATH || "";

async function handle<T>(res: Response, parse: (body: unknown) => T): Promise<T> {
  if (!res.ok) {
    let detail = "";
    try {
      const data = (await res.json()) as { detail?: unknown; message?: unknown };
      detail = String(data.detail ?? data.message ?? "").slice(0, 300);
    } catch {
      /* no body */
    }
    throw new ApiError(
      "http",
      detail ? `Backend ${res.status}: ${detail}` : `Backend error ${res.status}`,
      res.status
    );
  }
  const text = await res.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new ApiError(
      "parse",
      `Backend returned a non-JSON response (${res.status}): ${text.slice(0, 200)}`
    );
  }
  try {
    return parse(body);
  } catch (err) {
    const why = err instanceof Error ? err.message : String(err);
    throw new ApiError(
      "parse",
      `Backend response didn't match the expected shape: ${why} — body: ${text.slice(0, 200)}`
    );
  }
}

function guardNetwork(err: unknown, fallback: string): never {
  if (err instanceof DOMException && err.name === "AbortError") throw err;
  throw new ApiError("network", fallback);
}

/** GET /api/components — the single discovery door (primitives + graphs). */
export async function getComponents(): Promise<ComponentsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/components`, { cache: "no-store" });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as ComponentsStatus);
}

/** GET /api/graphs — saved graph documents. */
export async function getGraphs(): Promise<GraphsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/graphs`, { cache: "no-store" });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as GraphsStatus);
}

/** POST /api/graphs — save (create/overwrite) by id; invalid = 400. */
export async function saveGraph(
  id: string,
  name: string,
  doc: GraphDoc
): Promise<GraphsStatus["graphs"][number]> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/graphs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, name, graph: doc }),
    });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => {
    const body = b as
      | { graphs?: GraphsStatus["graphs"]; id?: string }
      | null;
    // Preferred: the family shape {active, graphs:[…]} that GET/DELETE/POST
    // all return since the parity fix — take the entry we just saved.
    if (body && Array.isArray(body.graphs)) {
      return body.graphs.find((g) => g.id === id) ?? (b as never);
    }
    // Compatibility: a pre-parity backend returned the saved entry *bare*
    // ({id,name,active,…}). Accept it when it is the entry we just saved, so
    // the canvas keeps working on such a server until it is restarted. (A
    // genuinely wrong payload still throws below, with a clear message.)
    if (body && typeof body === "object" && body.id === id) {
      return body as unknown as GraphsStatus["graphs"][number];
    }
    throw new ApiError(
      "parse",
      `Expected {{active, graphs:[…]}} or the saved entry for "${id}"; got ${JSON.stringify(b).slice(0, 160)}`
    );
  });
}

/** GET /api/graphs/{id} — fetch one saved document (for the canvas). */
export async function getGraph(id: string): Promise<{ id: string; name: string; graph: GraphDoc }> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/graphs/${encodeURIComponent(id)}`, { cache: "no-store" });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as { id: string; name: string; graph: GraphDoc });
}

/** DELETE /api/graphs/{id}. */
export async function deleteGraph(id: string): Promise<GraphsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/graphs/${encodeURIComponent(id)}`, { method: "DELETE" });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as GraphsStatus);
}

/** GET /api/graphs/{id}/last-run — 404 until the graph has run here. */
export async function getLastRun(id: string): Promise<LastRun> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/graphs/${encodeURIComponent(id)}/last-run`, {
      cache: "no-store",
    });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as LastRun);
}

/** POST /api/components/activate — swap components in at runtime. */
export async function activate(body: {
  preset?: string;
  slot?: string;
  alias?: string;
  loop?: string;
  graph_id?: string;
}): Promise<ComponentsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/components/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as ComponentsStatus);
}

/** POST /api/chat — one turn; the harness is stateless, the thread rides in. */
export async function postChat(
  message: string,
  conversation: { role: "user" | "agent"; content: string }[] = [],
  signal?: AbortSignal
): Promise<string> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, conversation }),
      signal,
    });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => String((b as { answer?: string }).answer ?? "").trim() || "No response received.");
}

/* ── Agent graphs (the capability-composition doc, docs/agent-graph.md) ─── */

/** GET /api/agents — saved agent documents (active + one line each). */
export async function getAgents(): Promise<AgentsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/agents`, { cache: "no-store" });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as AgentsStatus);
}

/** GET /api/agents/{id} — the full document (for the capability node view). */
export async function getAgent(id: string): Promise<AgentDetail> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/agents/${encodeURIComponent(id)}`, {
      cache: "no-store",
    });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as AgentDetail);
}

/** POST /api/agents — save (create/overwrite) the agent document for `id`.
 *  Body `{ id, name?, agent }`; the backend validates BEFORE persisting —
 *  invalid is a 400, never a saved file. Returns the family shape
 *  `{ active, agents: […] }`. */
export async function saveAgent(
  id: string,
  name: string | null,
  doc: AgentDocument
): Promise<AgentsStatus> {
  const body: { id: string; name?: string; agent: AgentDocument } = { id, agent: doc };
  if (name && name.trim()) body.name = name;
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/agents`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as AgentsStatus);
}

/** POST /api/agents/{id}/activate — make a saved agent the ACTIVE one.
 *  Bind-first: a connection/model/persona that can't be built is a VISIBLE
 *  400 (the previous activation untouched), never a silent empty toolset. */
export async function activateAgent(id: string): Promise<AgentsStatus> {
  let res: Response;
  try {
    res = await fetch(
      `${API_URL}/api/agents/${encodeURIComponent(id)}/activate`,
      { method: "POST" }
    );
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as AgentsStatus);
}

/** POST /api/agents/deactivate — clear the ACTIVE agent WITHOUT deleting
 *  the document (the live bind rolls back; the built-in pipeline comes back
 *  from the next turn). Returns the family shape `{ active, agents: […] }`. */
export async function deactivateAgent(): Promise<AgentsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/agents/deactivate`, { method: "POST" });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as AgentsStatus);
}

/** DELETE /api/agents/{id} — drop the saved document (404 when unknown).
 *  Returns the family shape `{ active, agents: […] }`. */
export async function deleteAgent(id: string): Promise<AgentsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/agents/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as AgentsStatus);
}

/** The `POST /api/agents/{id}/mcp/test` result: the alias's fresh
 *  connection check + the tools it actually advertised. */
export interface McpTestResult {
  alias: string;
  transport: string | null;
  detail: string;
  tools: string[];
}

/** POST /api/agents/{id}/mcp/test — a GENUINE connection check on one of
 *  the agent's declared aliases (it must re-earn the connection; an
 *  unreachable server is a visible 400, never a silent pass). */
export async function testAgentMcp(id: string, alias: string): Promise<McpTestResult> {
  let res: Response;
  try {
    res = await fetch(
      `${API_URL}/api/agents/${encodeURIComponent(id)}/mcp/test`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias }),
      }
    );
  } catch (err) {
    guardNetwork(err, "Could not reach the AI backend. Is it running?");
  }
  return handle(res, (b) => b as McpTestResult);
}

/** GET /health — the connection indicator. */
export async function isBackendOnline(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    if (!res.ok) return false;
    const data = (await res.json()) as { status?: string };
    return data.status === "ok";
  } catch {
    return false;
  }
}

/* ── Design face (face 1 — the GUTS MCP server, app/design/mcp_server.py) ──
 *
 * The designer talks to the design face AS AN MCP CLIENT: one JSON-RPC
 * `tools/call` to the `draft` tool per generation. In the default
 * same-origin setup the request lands on this Next server and rewrites()
 * forwards it to the design face (DESIGN_MCP_PROXY_TARGET —
 * `http://design-mcp:8090` in compose, `http://localhost:8090` for local
 * dev), so NO "localhost" is baked into the client. Cross-origin setups
 * (Pages export / legacy absolute API_URL) can set NEXT_PUBLIC_MCP_URL to
 * the design face's absolute URL instead.
 *
 * Response semantics (pinned by app/design/mcp_server.py + the repo's own
 * client, app/mcp/client.py): a RED verdict is a normal business result
 * (doc + verdict.ok:false — the face never fails on a bad doc); isError /
 * a JSON-RPC error means the tool or the transport failed (e.g. the model
 * is unreachable), which surfaces as an ApiError here.
 */

/** The `draft` tool's result: the candidate document + the green door's
 *  verdict. `doc` is a graph document (schemas/graph.py) — the canvas
 *  bridge is fromDoc()/toDoc() in lib/graph.ts. */
export interface DesignDraftResult {
  doc: Record<string, unknown>;
  verdict: { ok: boolean; issues: string[] };
}

const MCP_URL = process.env.NEXT_PUBLIC_MCP_URL || `${API_URL}/mcp`;

let mcpSeq = 0;

/** First {type:"text",text} entry of an MCP content array (the face
 *  always answers with exactly one text part — mcp_server.py). */
function firstContentText(content: unknown): string {
  if (!Array.isArray(content)) return "";
  for (const part of content) {
    if (
      part &&
      typeof part === "object" &&
      (part as { type?: unknown }).type === "text" &&
      typeof (part as { text?: unknown }).text === "string"
    ) {
      return (part as { text: string }).text;
    }
  }
  return "";
}

/** Parse a design-face JSON-RPC response into the draft result.
 *
 *  Exported so this repo's tests can pin the contract without a network:
 *  good result / visible tool error (isError) / JSON-RPC error / shape
 *  violations. Throws ApiError in every failure world. */
export function parseDesignResult(
  body: unknown,
  status: number
): DesignDraftResult {
  const msg = (body ?? {}) as {
    error?: { code?: number; message?: string };
    result?: { content?: unknown; isError?: boolean };
  };
  if (msg.error) {
    throw new ApiError(
      "http",
      `Design face ${msg.error.code ?? "error"}: ${msg.error.message ?? "unknown error"}`,
      status
    );
  }
  if (!msg.result) {
    throw new ApiError(
      "parse",
      `Design face returned neither a result nor an error (HTTP ${status})`
    );
  }
  const text = firstContentText(msg.result.content);
  if (msg.result.isError) {
    throw new ApiError("http", text || "Design tool failed", status);
  }
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new ApiError(
      "parse",
      `Design tool answered with non-JSON text (HTTP ${status}): ${text.slice(0, 200)}`
    );
  }
  const p = (payload ?? {}) as { doc?: unknown; verdict?: unknown };
  if (typeof p.doc !== "object" || p.doc === null || Array.isArray(p.doc)) {
    throw new ApiError("parse", "Design tool result had no `doc` object");
  }
  const v = (p.verdict ?? {}) as Record<string, unknown>;
  if (typeof v.ok !== "boolean") {
    throw new ApiError("parse", "Design tool result had no `verdict`");
  }
  return {
    doc: p.doc as Record<string, unknown>,
    verdict: {
      ok: v.ok,
      issues: Array.isArray(v.issues) ? v.issues.map(String) : [],
    },
  };
}

/** Draft a graph through the design face (MCP `draft` tool).
 *
 *  `notes`  — the business-case channel (face 0 contract: loop counts,
 *             stop conditions, priorities all ride here, never in the core).
 *  `current` — the working canvas as a document; when passed, the model may
 *             ground the draft in what is already on the canvas. The core
 *             stays stateless either way. */
export async function designDraft(
  goal: string,
  notes?: string,
  current?: Record<string, unknown>
): Promise<DesignDraftResult> {
  const args: Record<string, unknown> = { goal };
  if (notes && notes.trim()) args.notes = notes.trim();
  if (current) args.current = current;
  let res: Response;
  try {
    mcpSeq += 1;
    res = await fetch(MCP_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: mcpSeq,
        method: "tools/call",
        params: { name: "draft", arguments: args },
      }),
    });
  } catch (err) {
    guardNetwork(
      err,
      "Could not reach the design face. Is it running (design-mcp / python -m design.mcp_server --http)?"
    );
  }
  const text = await res.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    throw new ApiError(
      "parse",
      `Design face returned a non-JSON response (HTTP ${res.status}): ${text.slice(0, 200)}`
    );
  }
  if (!res.ok && !(body as { error?: unknown }).error) {
    throw new ApiError("http", `Design face HTTP ${res.status}`, res.status);
  }
  return parseDesignResult(body, res.status);
}
