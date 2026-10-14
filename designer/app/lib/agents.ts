/**
 * Agent-graph display helpers (docs/agent-graph.md — the layer above).
 *
 * Pure, stdlib-free functions that shape the backend's agent document into
 * what the Agents library panel renders. The backend validator
 * (app/schemas/agent.py) is the authority; this module only *reads* the
 * shape it produces (a saved document is structurally sound by definition).
 *
 * The one structural read that matters here is the load-bearing
 * distinction of the doc: the agent is a capability BUNDLE around exactly
 * one executable child (`loop`), rendered as `ref` (a saved turn-graph) or
 * `alias` (a built-in loop) — the only real "edge" the agent has.
 *
 * Run with:    npm test          (or: node --no-warnings --test app/lib/agents.test.mjs)
 * Requires:    Node ≥ 22.7 with type stripping enabled (Node ≥ 23.6 default).
 */

import type {
  AgentDocument,
  AgentMeta,
  GraphDoc,
} from "./types";
import type { CanvasGraph } from "./graph";

/** One line per agent (list view): the bundle's shape in the meta shape. */
export function summarizeAgent(a: AgentMeta): string {
  const parts: string[] = [];
  parts.push(a.loop ? `loop ${a.loop}` : "no loop");
  if (a.model) parts.push(`model ${a.model}`);
  parts.push(a.mcp.length > 0 ? `${a.mcp.length} mcp` : "no mcp");
  parts.push(a.skills.length > 0 ? `${a.skills.length} skill${a.skills.length > 1 ? "s" : ""}` : "no skills");
  if (a.has_prompt) parts.push("persona");
  const settingText = Object.entries(a.settings || {})
    .map(([k, v]) => `${k}=${v}`)
    .join(", ");
  if (settingText) parts.push(settingText);
  return parts.join(" · ");
}

export interface McpLine {
  alias: string;
  transport: string | null;
  endpoint: string | null;
}

export interface SkillLine {
  path: string;
  tools: string[];
}

/** The expanded bundle view: each capability the agent composes, as rows.
 *  The `loop` child is returned ALONE — it is the distinguished executable
 *  child (the only real edge of the agent graph), rendered expandable into
 *  the referenced turn-graph. */
export function describeBundle(doc: AgentDocument, agentId: string): {
  id: string;
  name: string;
  persona: boolean;
  model: { provider: string; id: string } | null;
  mcp: McpLine[];
  skills: SkillLine[];
  settings: string[];
  loop: { kind: "ref"; ref: string } | { kind: "alias"; alias: string };
} {
  const mcp: McpLine[] = (doc.mcp ?? []).map((e) => ({
    alias: e.alias,
    transport: e.transport ?? null,
    endpoint: e.url ?? (e.command ? e.command.join(" ") : null),
  }));
  const skills: SkillLine[] = (doc.skill ?? []).map((s) => ({
    path: s.path,
    tools: s.tools ?? [],
  }));
  const settings = Object.entries(doc.settings ?? {}).map(([k, v]) => `${k}=${v}`);
  const model = doc.model
    ? { provider: doc.model.provider ?? "?", id: doc.model.id ?? "?" }
    : null;

  const loop = doc.loop ?? {};
  let loopLine: { kind: "ref"; ref: string } | { kind: "alias"; alias: string };
  if (loop.ref) {
    loopLine = { kind: "ref", ref: loop.ref };
  } else if (loop.alias) {
    loopLine = { kind: "alias", alias: loop.alias };
  } else {
    // A saved document cannot reach this (the validator requires exactly one
    // loop), so degrade honestly: name it, never invent one.
    loopLine = { kind: "alias", alias: "(unspecified)" };
  }

  return {
    id: agentId,
    name: doc.name ?? agentId,
    persona: Boolean(doc.prompt),
    model,
    mcp,
    skills,
    settings,
    loop: loopLine,
  };
}

/** The loop-preview shape: what a mini view of one turn-graph renders. */
export interface LoopView {
  nodes: { id: string; primitive: string }[];
  edges: number;
}

/** Expand the agent's `loop` reference into the turn-graph's shape for the
 *  read-only mini view: the node list (id + primitive) and the edge count.
 *  The turn-graph is where execution happens — the agent is what binds it. */
export function describeLoopGraph(doc: GraphDoc): LoopView {
  const nodes = Object.entries(doc.nodes ?? {}).map(([id, n]) => ({
    id,
    primitive: n.primitive,
  }));
  return { nodes, edges: (doc.edges ?? []).length };
}

/** The SAME preview shape from the canvas's OWN working graph — the LIVE
 *  source for the agent card's loop preview when the loop slot names this
 *  canvas's graph: derived from local state (no fetch), always the
 *  "what it will run when saved" truth the save-order persists, and it
 *  re-renders as the loop is drawn. The canvas is where loops are BUILT;
 *  the preview is where the agent SHOWS its loop. */
export function describeLoopCanvas(canvas: CanvasGraph): LoopView {
  return {
    nodes: canvas.nodes.map((n) => ({ id: n.id, primitive: n.primitive })),
    edges: canvas.edges.length,
  };
}
