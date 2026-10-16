import {
  ApiError,
  type ActivateComponentsRequest,
  type AgentDetail,
  type AgentsStatus,
  type ChatRequest,
  type ChatResponse,
  type ComponentsStatus,
  type ConversationTurn,
  type McpTestResult,
} from "./types";

/** Backend base URL. Default (empty) → relative, proxied by this server
 *  (rewrites() in next.config.js), so remote browsers work too; set
 *  NEXT_PUBLIC_API_URL for direct absolute-URL behaviour. */
export const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

/**
 * Post one turn to the stateless harness API.
 *
 * The backend owns no conversation state: `conversation` (oldest first) is
 * the full prior thread, supplied by the caller each time.
 */
export async function postChat(
  message: string,
  conversation: ConversationTurn[],
  signal?: AbortSignal
): Promise<string> {
  const body: ChatRequest = { message, conversation };

  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(
      "network",
      "Could not reach the AI backend. Is it running?"
    );
  }

  if (!res.ok) {
    let detail = "";
    try {
      const data = await res.json();
      detail = (data.detail || data.message || "").toString().slice(0, 300);
    } catch {
      try {
        detail = (await res.text()).slice(0, 300);
      } catch {
        /* no body */
      }
    }
    throw new ApiError(
      "http",
      detail.length ? `Backend error ${res.status}: ${detail}` : `Backend error ${res.status}`,
      res.status
    );
  }

  let data: ChatResponse;
  try {
    data = (await res.json()) as ChatResponse;
  } catch {
    throw new ApiError("parse", "Backend returned an unreadable response.");
  }

  const answer = (data.answer || "").trim() || "No response received.";
  return answer;
}

/** Read the live agent component configuration (slots + presets). */
export async function getComponents(): Promise<ComponentsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/components`, { cache: "no-store" });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("network", "Could not reach the AI backend. Is it running?");
  }

  if (!res.ok) {
    let detail = "";
    try {
      const data = await res.json();
      detail = (data.detail || data.message || "").toString().slice(0, 300);
    } catch {
      /* no body */
    }
    throw new ApiError("http", detail ? `Backend error ${res.status}: ${detail}` : `Backend error ${res.status}`, res.status);
  }

  try {
    return (await res.json()) as ComponentsStatus;
  } catch {
    throw new ApiError("parse", "Backend returned an unreadable component status.");
  }
}

/** Swap components in at runtime: a whole preset or one slot alias. */
export async function activateComponents(
  request: ActivateComponentsRequest
): Promise<ComponentsStatus> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/components/activate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("network", "Could not reach the AI backend. Is it running?");
  }

  if (!res.ok) {
    let detail = "";
    try {
      const data = await res.json();
      detail = (data.detail || data.message || "").toString().slice(0, 300);
    } catch {
      /* no body */
    }
    throw new ApiError("http", detail ? `Backend error ${res.status}: ${detail}` : `Backend error ${res.status}`, res.status);
  }

  try {
    return (await res.json()) as ComponentsStatus;
  } catch {
    throw new ApiError("parse", "Backend returned an unreadable component status.");
  }
}

/* ── Agent graphs (the capability-composition document) ─────────────────── */

async function agentRequest<T>(
  path: string,
  init?: RequestInit
): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      cache: "no-store",
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError("network", "Could not reach the AI backend. Is it running?");
  }

  if (!res.ok) {
    let detail = "";
    try {
      const data = await res.json();
      detail = (data.detail || data.message || "").toString().slice(0, 300);
    } catch {
      try {
        detail = (await res.text()).slice(0, 300);
      } catch {
        /* no body */
      }
    }
    throw new ApiError(
      "http",
      detail.length ? `Backend error ${res.status}: ${detail}` : `Backend error ${res.status}`,
      res.status
    );
  }

  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("parse", "Backend returned an unreadable response.");
  }
}

/** List the saved agent documents + which one is active. */
export async function getAgents(): Promise<AgentsStatus> {
  return agentRequest<AgentsStatus>("/api/agents");
}

/** Fetch one saved agent document by id (id + name + full document). */
export async function getAgent(agentId: string): Promise<AgentDetail> {
  return agentRequest<AgentDetail>(`/api/agents/${encodeURIComponent(agentId)}`);
}

/** Make a saved agent the ACTIVE one. Returns the refreshed list. */
export async function activateAgent(agentId: string): Promise<AgentsStatus> {
  return agentRequest<AgentsStatus>(`/api/agents/${encodeURIComponent(agentId)}/activate`, {
    method: "POST",
  });
}

/** Clear the ACTIVE agent WITHOUT deleting the document — the live bind
 *  rolls back and the built-in pipeline runs from the next turn. Returns
 *  the refreshed list (active is now null). */
export async function deactivateAgent(): Promise<AgentsStatus> {
  return agentRequest<AgentsStatus>("/api/agents/deactivate", {
    method: "POST",
  });
}

/** Test one MCP connection an agent declares (the panel's "test" door). */
export async function testAgentMcp(
  agentId: string,
  alias: string
): Promise<McpTestResult> {
  return agentRequest<McpTestResult>(
    `/api/agents/${encodeURIComponent(agentId)}/mcp/test`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ alias }),
    }
  );
}

/** Liveness probe for the connection indicator. Resolves to a boolean. */
export async function isBackendOnline(): Promise<boolean> {
  try {
    const res = await fetch(`${API_URL}/health`, { cache: "no-store" });
    if (!res.ok) return false;
    const data = await res.json();
    return data.status === "ok";
  } catch {
    return false;
  }
}
