/**
 * API client for the learning-ai-harness backend (designer surface).
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

import {
  ApiError,
  type AgentDetail,
  type AgentsStatus,
  type ComponentsStatus,
  type GraphDoc,
  type GraphsStatus,
  type LastRun,
} from "./types";

/** Re-exported: client code that catches backend errors (e.g. the live-run
 *  poller, which must ignore 404s) imports it from one door — this module. */
export { ApiError };

/** Backend base URL.
 *
 * Default (empty) → relative URLs: requests are same-origin and the Next
 * server proxies them to the backend (rewrites() in next.config.js). That
 * works even when the browser is on a *different machine* than the backend —
 * never bake "localhost" into the client. Set NEXT_PUBLIC_API_URL to an
 * absolute URL to talk to the backend directly (legacy behaviour). */
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

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
