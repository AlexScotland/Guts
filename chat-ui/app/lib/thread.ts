import type { Message, Role } from "./types";

/**
 * The frontend OWNS the conversation thread (the backend is stateless), so
 * persistence lives here. localStorage is enough for v1: no DB, survives
 * reload/restart, cleared explicitly by the user.
 */

const STORAGE_KEY = "learning-ai-harness.thread.v1";
const MAX_STORED_MESSAGES = 400; // guard against unbounded growth

function isMessage(m: unknown): m is Message {
  if (!m || typeof m !== "object") return false;
  const o = m as Record<string, unknown>;
  const role = o.role as Role;
  return (
    (role === "user" || role === "agent") &&
    typeof o.content === "string" &&
    typeof o.timestamp === "number"
  );
}

export function loadThread(): Message[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isMessage).slice(-MAX_STORED_MESSAGES);
  } catch {
    return [];
  }
}

export function saveThread(messages: Message[]): void {
  if (typeof window === "undefined") return;
  try {
    const trimmed = messages.slice(-MAX_STORED_MESSAGES);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {
    // Quota exceeded or storage blocked — the thread still works this session.
  }
}

export function clearThread(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
