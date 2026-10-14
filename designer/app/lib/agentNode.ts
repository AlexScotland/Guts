/**
 * agentNode.ts — the canvas AGENT NODE contract (docs/agent-graph.md,
 * "The agent node on the canvas" — the designer's authoring surface).
 *
 * The agent node is a design-LAYER draft of ONE agent document: the
 * capability bundle (persona / model / mcp / skills / settings) around
 * exactly one `loop` slot — a saved turn-graph `ref` XOR a built-in
 * `alias`. It NEVER enters the turn-graph document: the canvas's `toDoc()`
 * keeps emitting 9-primitive nodes and edges only, and this module's output
 * travels to POST /api/agents, where app/schemas/agent.py remains the
 * authority (a 400 is the honest save failure).
 *
 * The closed enums below are MIRRORs of the backend's (app/schemas/agent.py)
 * so the canvas renders the honest options offline; they are preflight
 * convenience, never a new authority.
 *
 * Run with:    npm test          (or: node --no-warnings --test app/lib/agentNode.test.mjs)
 * Requires:    Node ≥ 22.7 with type stripping enabled (Node ≥ 23.6 default).
 */

import type { AgentDocument, AgentMcpEntry } from "./types";
import type { CheckIssue } from "./graph";

// ── closed vocab mirrors (backend: app/schemas/agent.py, the authority) ────

/** Model providers accepted in v1 (closed enum — extend deliberately). */
export const AGENT_PROVIDERS = ["ollama"];

/** Built-in loop-slot aliases: `loop.alias` must name one of these. */
export const AGENT_LOOP_ALIASES = ["plan_execute", "direct"];

/** The six slot names: `settings` keys must be a subset of these. */
export const AGENT_SLOT_NAMES = [
  "goal_resolver",
  "planner",
  "executor",
  "evaluator",
  "memory",
  "loop",
];

/** The agent-id token rule (components/agent_store.py's save rule). */
const AGENT_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

/** The same rule as a plain check — the canvas rename commit validates with
 *  this before it rehomes the node (an id is the agent's REST identity). */
export function isValidAgentId(id: string): boolean {
  return AGENT_ID_RE.test(id);
}

// ── the draft model (what the canvas agent node EDITs) ────────────────────

export type McpTransport = "" | "stdio" | "sse" | "http";

export interface AgentMcpRow {
  alias: string;
  transport: McpTransport;
  url: string; // sse | http
  command: string; // stdio: argv, whitespace-separated
}

export interface AgentSkillRow {
  path: string;
  tools: string; // comma-separated tool names ("" = instructions only)
}

export interface AgentSettingRow {
  slot: string;
  alias: string;
}

/** The one child that runs — EXACTLY one of the two (a discriminated union,
 *  so the ref/alias XOR lives in the SHAPE, as it does in the schema). */
export type AgentLoopSlot =
  | { mode: "ref"; ref: string }
  | { mode: "alias"; alias: string };

export interface AgentDraft {
  name: string;
  prompt: string;
  model: { provider: string; id: string } | null;
  mcp: AgentMcpRow[];
  skill: AgentSkillRow[];
  settings: AgentSettingRow[];
  loop: AgentLoopSlot;
}

/** A positioned canvas agent node: identity (its saved-agent REST id) +
 *  canvas-only position + the draft it edits. */
export interface CanvasAgent {
  id: string;
  x: number;
  y: number;
  draft: AgentDraft;
}

// ── factories ──────────────────────────────────────────────────────────────

/** A fresh, VALID minimal agent: the built-in alias loop, nothing else —
 *  it serializes to exactly `{ loop: { alias: "plan_execute" } }`. */
export function newAgentDraft(): AgentDraft {
  return {
    name: "",
    prompt: "",
    model: null,
    mcp: [],
    skill: [],
    settings: [],
    loop: { mode: "alias", alias: AGENT_LOOP_ALIASES[0] },
  };
}

export function newMcpRow(): AgentMcpRow {
  return { alias: "", transport: "", url: "", command: "" };
}

export function newSkillRow(): AgentSkillRow {
  return { path: "", tools: "" };
}

export function newSettingRow(): AgentSettingRow {
  return { slot: "", alias: "" };
}

/** The next free `agentN` id, skipping `existing`. */
export function newAgentId(existing: string[]): string {
  const used = new Set(existing);
  for (let i = 1; ; i++) {
    const candidate = `agent${i}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** The loop slot's display line (the card chip + the panel header):
 *  a ref names the SAVED graph, an alias names the BUILT-IN loop. */
export function loopSlotLine(slot: AgentLoopSlot): string {
  return slot.mode === "ref"
    ? `saved graph "${slot.ref}"`
    : `built-in "${slot.alias}"`;
}

// ── draft ⇄ document (the REST door's exact shape) ────────────────────────

/** Serialize the draft to the agent document for `POST /api/agents`
 *  (body: `{ id, name, agent: <doc> }`). Empties are OMITTED — a saved
 *  document stays minimal and the backend validator stays the authority.
 *  The loop slot lands as `{ref}` or `{alias}` — never both, never neither. */
export function draftToDoc(draft: AgentDraft): AgentDocument {
  const doc: AgentDocument = {};

  if (draft.name.trim()) doc.name = draft.name.trim();
  if (draft.prompt.trim()) doc.prompt = draft.prompt.trim();

  if (draft.model && draft.model.provider && draft.model.id.trim()) {
    doc.model = { provider: draft.model.provider, id: draft.model.id.trim() };
  }

  if (draft.mcp.length > 0) {
    doc.mcp = draft.mcp.map((row) => {
      const entry: AgentMcpEntry = { alias: row.alias.trim() };
      if (row.transport) entry.transport = row.transport;
      if (row.transport === "stdio") {
        const argv = row.command.split(/\s+/).filter(Boolean);
        if (argv.length > 0) entry.command = argv;
      } else if (row.url.trim()) {
        entry.url = row.url.trim();
      }
      return entry;
    });
  }

  if (draft.skill.length > 0) {
    doc.skill = draft.skill.map((row) => {
      const entry: { path: string; tools?: string[] } = { path: row.path.trim() };
      const tools = row.tools
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      if (tools.length > 0) entry.tools = tools;
      return entry;
    });
  }

  const settings: Record<string, string> = {};
  for (const row of draft.settings) {
    if (row.slot) settings[row.slot] = row.alias;
  }
  if (Object.keys(settings).length > 0) doc.settings = settings;

  doc.loop =
    draft.loop.mode === "ref"
      ? { ref: draft.loop.ref.trim() }
      : { alias: draft.loop.alias };

  return doc;
}

/** Load a SAVED agent document (GET /api/agents/{id}) into a clean draft.
 *  The inverse of `draftToDoc` — a round-trip keeps the document valid
 *  (a saved document is structurally sound by definition). */
export function docToDraft(doc: AgentDocument): AgentDraft {
  return {
    name: doc.name ?? "",
    prompt: doc.prompt ?? "",
    model: doc.model
      ? {
          provider: doc.model.provider ?? AGENT_PROVIDERS[0],
          id: doc.model.id ?? "",
        }
      : null,
    mcp: (doc.mcp ?? []).map((e) => ({
      alias: e.alias,
      transport: (e.transport ?? "") as McpTransport,
      url: e.url ?? "",
      command: (e.command ?? []).join(" "),
    })),
    skill: (doc.skill ?? []).map((s) => ({
      path: s.path,
      tools: (s.tools ?? []).join(", "),
    })),
    settings: Object.entries(doc.settings ?? {}).map(([slot, alias]) => ({
      slot,
      alias,
    })),
    loop: doc.loop?.ref
      ? { mode: "ref", ref: doc.loop.ref }
      : doc.loop?.alias
      ? { mode: "alias", alias: doc.loop.alias }
      : { mode: "alias", alias: AGENT_LOOP_ALIASES[0] },
  };
}

// ── preflight (a MIRROR of the validator's cheap rules — never an authority)

/** Cheap structural checks over the draft — the same rules the backend
 *  validator enforces (loop XOR + known aliases, transport pairing, alias
 *  uniqueness, settings slots ⊆ the six, provider enum, the id token rule),
 *  surfaced BEFORE the save so the author sees the 400's reason early.
 *  The server validator remains the authority when it is reachable. */
export function checkAgentDraft(
  draft: AgentDraft,
  agentId: string
): CheckIssue[] {
  const issues: CheckIssue[] = [];
  const err = (message: string) => issues.push({ severity: "error", message });

  if (!AGENT_ID_RE.test(agentId)) {
    err(
      `Agent id "${agentId}" must be a [A-Za-z0-9_-] token (first char a letter or digit) — that is the saved agent's identity.`
    );
  }

  if (draft.name !== "" && draft.name.trim() === "") {
    err("name: must be non-empty when set.");
  }
  if (draft.prompt !== "" && draft.prompt.trim() === "") {
    err("prompt: must be non-empty when set.");
  }

  if (draft.model) {
    if (!AGENT_PROVIDERS.includes(draft.model.provider)) {
      err(
        `model.provider: must be one of ${AGENT_PROVIDERS.join(", ")} (closed v1 enum); got "${draft.model.provider}".`
      );
    }
    if (!draft.model.id.trim()) {
      err("model.id: must be a non-empty string.");
    }
  }

  const aliases: string[] = [];
  draft.mcp.forEach((row, i) => {
    const label = `mcp[${i}]`;
    if (!row.alias.trim()) {
      err(`${label}: an alias is required (or remove the row).`);
      return;
    }
    aliases.push(row.alias.trim());
    if (!row.transport) {
      err(`${label}: pick a transport — stdio, sse, or http.`);
      return;
    }
    if (row.transport === "stdio") {
      if (!row.command.trim()) {
        err(`${label}: transport "stdio" needs a command (argv).`);
      }
    } else if (!row.url.trim()) {
      err(`${label}: transport "${row.transport}" needs a URL.`);
    }
  });
  const dupes = [
    ...new Set(
      aliases.filter((a) => aliases.indexOf(a) !== aliases.lastIndexOf(a))
    ),
  ];
  if (dupes.length > 0) {
    err(`mcp: alias must be unique within the agent; duplicates: ${dupes.join(", ")}.`);
  }

  draft.skill.forEach((row, i) => {
    if (!row.path.trim()) {
      err(`skill[${i}]: a path under the app root is required (or remove the row).`);
    }
    if (row.tools !== "" && row.tools.split(",").every((t) => !t.trim())) {
      err(`skill[${i}]: tools, when given, must be a non-empty comma-separated list.`);
    }
  });

  draft.settings.forEach((row, i) => {
    if (!AGENT_SLOT_NAMES.includes(row.slot)) {
      err(
        `settings[${i}]: unknown slot "${row.slot || "?"}" — the six slots: ${AGENT_SLOT_NAMES.join(", ")}.`
      );
    } else if (!row.alias.trim()) {
      err(`settings[${i}]: the alias for slot "${row.slot}" must be non-empty.`);
    }
  });

  if (draft.loop.mode === "ref") {
    if (!AGENT_ID_RE.test(draft.loop.ref)) {
      err(
        "loop: ref must name a saved graph id ([A-Za-z0-9_-] token) — the one child that runs; an agent with no loop can't run a turn."
      );
    }
  } else if (!AGENT_LOOP_ALIASES.includes(draft.loop.alias)) {
    err(
      `loop: alias must be one of ${AGENT_LOOP_ALIASES.join(", ")}; got "${draft.loop.alias}".`
    );
  }

  return issues;
}
