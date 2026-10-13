/* Shared types for the chat frontend. */

export type Role = "user" | "agent";

export interface Message {
  role: Role;
  content: string;
  timestamp: number;
  /** True when this message is a rendered error (API/network failure). */
  error?: boolean;
}

/** One prior turn of the thread, sent with every POST (backend contract). */
export interface ConversationTurn {
  role: Role;
  content: string;
}

export interface ChatRequest {
  message: string;
  conversation: ConversationTurn[];
}

export interface ChatResponse {
  answer: string;
}

/* ── Component hot-swap (backend /api/components contract) ── */

export interface ComponentAlias {
  alias: string;
  version: string;
  description: string;
}

export interface SlotInfo {
  active: string | null;
  available: ComponentAlias[];
}

/** Response of GET /api/components and POST /api/components/activate. */
export interface ComponentsStatus {
  slots: Record<string, SlotInfo>;
  presets: Record<string, Record<string, string>>;
}

export interface ActivateComponentsRequest {
  preset?: string;
  slot?: string;
  alias?: string;
}

/** Thrown by the api client for any non-2xx or network failure. */
export class ApiError extends Error {
  readonly kind: "network" | "http" | "parse";

  constructor(kind: ApiError["kind"], message: string, readonly status?: number) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
  }
}

/** Backend message caps (keep in sync with app/server.py). */
export const MAX_MESSAGE_LENGTH = 10_000;
export const MAX_TURN_LENGTH = 50_000;
