/**
 * Canvas model + helpers for the graph documents (frozen v0 contract).
 *
 * The canvas is a positional wrapper over the JSON document:
 *   doc  : { name?, version?, budget?, max_parallel?, nodes: {id: spec}, edges: [] }
 *   graph: { id, name, budget, max_parallel, nodes: CanvasNode[], edges: [] }
 *
 * Positions are canvas-only (never persisted into the document) — the
 * document stays the single source of truth and stays diffable as data.
 *
 * Port/typing rules mirror app/schemas/graph.py (the authority): the
 * closed type set, `goal` as the graph input (never an edge), control
 * edges only from verdict nodes (critic/gate) with max_passes >= 1.
 */

import type {
  BranchSpec,
  EdgeSpec,
  GraphDoc,
  NodeSpec,
  PortType,
} from "./types";

// ── canvas model ─────────────────────────────────────────────────────────

export interface CanvasNode {
  id: string;
  primitive: string;
  x: number;
  y: number;
  config: Record<string, unknown>;
  budget: number | null;
  /** "abort" | "skip" | "retry(n)" — the document's own string form. */
  on_failure?: string;
  branches: BranchSpec[];
}

export interface CanvasEdge {
  from: string;
  to: string;
  type: "data" | "control";
  max_passes?: number;
}

export interface CanvasGraph {
  id: string;
  name: string;
  budget: number | null;
  max_parallel: number | null;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
}

// ── geometry ─────────────────────────────────────────────────────────────

export const NODE_W = 236;
export const NODE_HEAD = 38;
export const ROW_STEP = 22;

/** CLICK vs DRAG threshold (client px, i.e. pointer-event coordinates).
 *  A pointerdown→pointerup whose total movement stays within this radius is
 *  a CLEAN CLICK (it selects / keeps the node inspector open); beyond it the
 *  grab is a drag (and dismisses the inspector).
 *
 *  `pointermove` always precedes `pointerup`, and a human click carries
 *  1–4 px of hand jitter between press and release — so the FIRST
 *  pointermove can never be treated as "a real move". The old code did
 *  exactly that (any move deselected + nudged the node), which made the
 *  on-node inspector close on every plain click ("can't click it, it keeps
 *  closing"). A true drag comfortably exceeds this within a few px. */
export const CLICK_DRAG_THRESHOLD = 4;

export interface Pt {
  x: number;
  y: number;
}

/** True while the grab is still a clean click: the movement from `start` to
 *  `cur` (client px) is within CLICK_DRAG_THRESHOLD. */
export function grabIsCleanClick(
  start: Pt,
  cur: Pt,
  threshold = CLICK_DRAG_THRESHOLD
): boolean {
  const dx = cur.x - start.x;
  const dy = cur.y - start.y;
  return dx * dx + dy * dy <= threshold * threshold;
}

const GOAL: PortType = "goal";

/** Top of row band ``i`` (inside the node card, whose top is node.y). */
export function rowTop(i: number): number {
  return NODE_HEAD + 2 + i * ROW_STEP;
}

/** Vertical center of port on row ``i`` (in graph/canvas coordinates). */
export function anchorY(i: number): number {
  return NODE_HEAD + 10 + i * ROW_STEP;
}

/** Control (repeat) port sits one band below the last input/output row. */
export function controlAnchorY(rows: number): number {
  return anchorY(rows) + 14;
}

export function nodeHeight(rows: number): number {
  return NODE_HEAD + Math.max(rows, 1) * ROW_STEP + 12;
}

/** Connectable input types for a primitive = requires/requires_any/accepts
 *  minus `goal` (goal is the graph input — it never rides an edge). */
export function inputTypes(primitive: string, meta: Map<string, PrimitiveMeta>): PortType[] {
  const m = meta.get(primitive);
  if (!m) return [];
  const seen = new Set<PortType>();
  const add = (t: PortType) => {
    if (t !== GOAL) seen.add(t);
  };
  for (const t of m.requires) add(t);
  for (const group of m.requires_any) for (const t of group) add(t);
  for (const t of m.accepts) add(t);
  return [...seen];
}

export function outputTypes(primitive: string, meta: Map<string, PrimitiveMeta>): PortType[] {
  return [...(meta.get(primitive)?.provides ?? [])];
}

/** Verdict nodes (critic / gate) own control edges (declared repeats). */
export function isControlSource(primitive: string): boolean {
  return primitive === "critic" || primitive === "gate";
}

/** Route nodes own CHOICE edges: their outgoing data edges are the choice
 *  set (v1, docs/ai-routing-nodes.md) — rendered as a dashed fan. */
export function isRouteSource(primitive: string): boolean {
  return primitive === "route";
}

// ── primitive metadata (one local table — the API block, when online, wins)

export interface PrimitiveMeta {
  primitive: string;
  description: string;
  requires: PortType[];
  requires_any: PortType[][];
  accepts: PortType[];
  provides: PortType[];
  side_effects: boolean;
}

const CONTROL_OUT = new Set(["critic", "gate"]);

/** Mirrors the backend PRIMITIVES table (app/schemas/graph.py) so the
 *  palette renders even when the API is offline. When reachable, the
 *  API's `primitives` block (GET /api/components) is authoritative. */
export const FALLBACK_PRIMITIVES: PrimitiveMeta[] = [
  {
    primitive: "prompt",
    description: "Inject a fixed prompt text into the context; needs no edges.",
    requires: [],
    requires_any: [],
    accepts: ["goal"],
    provides: ["context"],
    side_effects: false,
  },
  {
    primitive: "research",
    description: "Read-only research (web / local knowledge) via the executor slot.",
    requires: ["goal"],
    requires_any: [],
    accepts: ["goal", "context"],
    provides: ["context", "result"],
    side_effects: false,
  },
  {
    primitive: "plan",
    description: "Produce a task list with the active planner slot.",
    requires: ["goal"],
    requires_any: [],
    accepts: ["goal", "context"],
    provides: ["plan"],
    side_effects: false,
  },
  {
    primitive: "act",
    description: "Do the work with the active executor slot. Needs a plan OR context.",
    requires: [],
    requires_any: [["plan", "context"]],
    accepts: ["plan", "context", "goal"],
    provides: ["result"],
    side_effects: true,
  },
  {
    primitive: "critic",
    description: "Judge the result: a verdict (+ pass); may drive repeats.",
    requires: ["result"],
    requires_any: [],
    accepts: ["result", "goal"],
    provides: ["verdict", "pass"],
    side_effects: false,
  },
  {
    primitive: "gate",
    description: "Project a verdict into a bare pass signal.",
    requires: ["verdict"],
    requires_any: [],
    accepts: ["verdict"],
    provides: ["pass"],
    side_effects: false,
  },
  {
    primitive: "merge",
    description: "Commit upstream outputs to the conversation, declared order.",
    requires: [],
    requires_any: [],
    accepts: ["result", "merged"],
    provides: ["merged", "result", "context"],
    side_effects: false,
  },
  {
    primitive: "parallel",
    description: "Run 1+ inline branch graphs concurrently; no side-effect nodes inside.",
    requires: ["goal"],
    requires_any: [],
    accepts: ["goal", "context"],
    provides: ["merged"],
    side_effects: false,
  },
  {
    primitive: "route",
    description:
      "(v1) Choose exactly one outgoing branch (manual/llm decider); the payload passes through.",
    requires: [],
    requires_any: [],
    accepts: ["goal", "context", "result"],
    provides: ["context", "result"],
    side_effects: false,
  },
];

export const CONTROL_SOURCES = CONTROL_OUT;

/** A data edge node→node is typable when the source provides one of the
 *  target's accepted types (goal excluded — it is the graph input). */
export function edgeTypable(
  source: string,
  target: string,
  meta: Map<string, PrimitiveMeta>
): boolean {
  const provides = meta.get(source)?.provides ?? [];
  const accepts = [...(meta.get(target)?.accepts ?? [])].filter((t) => t !== GOAL);
  return provides.some((t) => accepts.includes(t));
}

// ── document ⇄ canvas ─────────────────────────────────────────────────────

const TOKEN_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

export function nextNodeId(existing: string[]): string {
  const used = new Set(existing);
  for (let i = 1; ; i++) {
    const candidate = `node${i}`;
    if (!used.has(candidate)) return candidate;
  }
}

/** Build a positioned canvas from a saved document. Layout: topological
 *  depth over data edges → column; declaration order → row. */
export function fromDoc(
  id: string,
  doc: GraphDoc,
  positions?: Record<string, { x: number; y: number }>
): CanvasGraph {
  const nodes: CanvasNode[] = Object.entries(doc.nodes).map(([nid, spec]) => ({
    id: nid,
    primitive: spec.primitive,
    x: 0,
    y: 0,
    config: { ...(spec.config ?? {}) },
    budget: spec.budget ?? null,
    on_failure: spec.on_failure,
    branches: spec.branches ? JSON.parse(JSON.stringify(spec.branches)) : [],
  }));

  const byId = new Map(nodes.map((n) => [n.id, n]));
  const dataIn = new Map<string, string[]>();
  for (const n of nodes) dataIn.set(n.id, []);
  for (const e of doc.edges ?? []) {
    if (e.type !== "data") continue;
    if (byId.has(e.to) && byId.has(e.from)) dataIn.get(e.to)!.push(e.from);
  }
  const depth = new Map<string, number>();
  const resolve = (nid: string, guard = 0): number => {
    if (depth.has(nid) || guard > 32) return depth.get(nid) ?? 0;
    const preds = dataIn.get(nid) ?? [];
    const d = preds.length ? Math.max(...preds.map((p) => resolve(p, guard + 1))) + 1 : 0;
    depth.set(nid, d);
    return d;
  };
  let maxDepth = 0;
  for (const n of nodes) maxDepth = Math.max(maxDepth, resolve(n.id));

  const columns = new Map<number, number>();
  let x = 60;
  for (let d = 0; d <= Math.max(0, maxDepth); d++) {
    columns.set(d, x);
    x += 330;
  }
  const rows = new Map<number, number>();
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    const row = (rows.get(d) ?? 0) + 1;
    rows.set(d, row);
  }
  for (const n of nodes) {
    const d = depth.get(n.id) ?? 0;
    const pos = positions?.[n.id];
    if (pos) {
      n.x = pos.x;
      n.y = pos.y;
    } else {
      n.x = (columns.get(d) ?? 60) + (depth.size ? 0 : 0);
      n.y = 50 + (rows.get(d) ?? 1) * 170;
      // stagger columns slightly so same-depth nodes don't overlap
      const siblings = nodes.filter((m) => (depth.get(m.id) ?? 0) === d);
      const idx = siblings.findIndex((m) => m.id === n.id);
      n.y = 50 + (idx + 1) * 170;
    }
  }

  const edges: CanvasEdge[] = (doc.edges ?? []).map((e) => ({
    from: e.from,
    to: e.to,
    type: e.type === "control" ? "control" : "data",
    max_passes: e.type === "control" ? e.max_passes : undefined,
  }));

  return {
    id,
    name: doc.name ?? id,
    budget: doc.budget ?? null,
    max_parallel: doc.max_parallel ?? null,
    nodes,
    edges,
  };
}

/** Serialize the canvas back to the document shape (positions dropped). */
export function toDoc(g: CanvasGraph): GraphDoc {
  const nodes: Record<string, NodeSpec> = {};
  for (const n of g.nodes) {
    const spec: NodeSpec = { primitive: n.primitive };
    if (Object.keys(n.config).length > 0) spec.config = n.config;
    if (n.budget !== null && n.budget !== undefined) spec.budget = n.budget;
    if (n.on_failure) spec.on_failure = n.on_failure;
    if (n.branches.length > 0) spec.branches = JSON.parse(JSON.stringify(n.branches));
    nodes[n.id] = spec;
  }
  const edges: EdgeSpec[] = g.edges.map((e) =>
    e.type === "control"
      ? { from: e.from, to: e.to, type: "control", max_passes: e.max_passes ?? 1 }
      : { from: e.from, to: e.to, type: "data" }
  );
  const doc: GraphDoc = { nodes, edges };
  if (g.name) doc.name = g.name;
  doc.version = "1.0";
  if (g.budget !== null && g.budget !== undefined) doc.budget = g.budget;
  if (g.max_parallel !== null && g.max_parallel !== undefined) doc.max_parallel = g.max_parallel;
  return doc;
}

// ── client-side sanity checks (the server validator is the authority) ──

export interface CheckIssue {
  severity: "error" | "warn";
  message: string;
}

/** Cheap structural checks that catch the obvious before save; the real
 *  authority is the server validator (POST /api/graphs → 400 detail). */
export function checkCanvas(g: CanvasGraph, meta: Map<string, PrimitiveMeta>): CheckIssue[] {
  const issues: CheckIssue[] = [];
  if (g.nodes.length === 0) {
    issues.push({ severity: "error", message: "Add at least one node." });
    return issues;
  }
  const byId = new Map(g.nodes.map((n) => [n.id, n]));
  for (const n of g.nodes) {
    if (!TOKEN_RE.test(n.id))
      issues.push({
        severity: "error",
        message: `Node id "${n.id}" must be a [A-Za-z0-9_-] token.`,
      });
    if (!meta.has(n.primitive))
      issues.push({
        severity: "error",
        message: `Unknown primitive "${n.primitive}" on node "${n.id}".`,
      });
    if (n.primitive === "parallel" && n.branches.length === 0)
      issues.push({
        severity: "error",
        message: `Node "${n.id}": parallel needs at least one branch.`,
      });
    // route (v1, docs/ai-routing-nodes.md): the choice set is the outgoing
    // data edges and config.options must equal it EXACTLY — same rules the
    // server validator enforces (the authority), mirrored cheaply here.
    if (n.primitive === "route") {
      const outs = g.edges.filter((e) => e.from === n.id && e.type === "data").map((e) => e.to);
      const cfg = (n.config ?? {}) as Record<string, unknown>;
      if (outs.length < 2)
        issues.push({
          severity: "error",
          message: `Node "${n.id}": route needs at least two outgoing data edges (its choice set); one target is a plain edge.`,
        });
      const options = (cfg.options ?? null) as Record<string, unknown> | null;
      if (!options || typeof options !== "object" || Array.isArray(options) || Object.keys(options).length === 0) {
        issues.push({
          severity: "error",
          message: `Node "${n.id}": route needs config.options mapping EVERY choice-set branch id to a non-empty description.`,
        });
      } else {
        const keys = Object.keys(options);
        const extra = keys.filter((k) => !outs.includes(k));
        const missing = outs.filter((o) => !keys.includes(o));
        if (extra.length || missing.length)
          issues.push({
            severity: "error",
            message: `Node "${n.id}": config.options keys must equal the choice set ${JSON.stringify(
              outs
            )} (extra: ${JSON.stringify(extra)}, missing: ${JSON.stringify(missing)}).`,
          });
        for (const k of keys)
          if (typeof options[k] !== "string" || !options[k].trim())
            issues.push({
              severity: "error",
              message: `Node "${n.id}": config.options[${JSON.stringify(k)}] must be a non-empty description.`,
            });
      }
      if (cfg.decider === "rule")
        issues.push({
          severity: "error",
          message: `Node "${n.id}": decider "rule" is reserved (unimplemented).`,
        });
      else if (cfg.decider !== "manual" && cfg.decider !== "llm")
        issues.push({
          severity: "error",
          message: `Node "${n.id}": route needs config.decider = "manual" or "llm".`,
        });
      if (cfg.fallback != null && !outs.includes(cfg.fallback as string))
        issues.push({
          severity: "error",
          message: `Node "${n.id}": config.fallback ${JSON.stringify(cfg.fallback)} is not in the choice set ${JSON.stringify(outs)}.`,
        });
      if (cfg.decider === "manual") {
        if (cfg.pinned == null)
          issues.push({
            severity: "error",
            message: `Node "${n.id}": the manual decider needs config.pinned naming one of the choice set ${JSON.stringify(outs)}.`,
          });
        else if (!outs.includes(cfg.pinned as string))
          issues.push({
            severity: "error",
            message: `Node "${n.id}": config.pinned ${JSON.stringify(cfg.pinned)} is not in the choice set ${JSON.stringify(outs)}.`,
          });
      } else if (cfg.pinned != null)
        issues.push({
          severity: "error",
          message: `Node "${n.id}": config.pinned is only valid with the manual decider.`,
        });
    }
  }
  const seen = new Set<string>();
  for (const e of g.edges) {
    const key = `${e.from}->${e.to}:${e.type}`;
    if (e.from === e.to)
      issues.push({ severity: "error", message: `Edge ${e.from} → ${e.to}: self-edge not allowed.` });
    if (!byId.has(e.from) || !byId.has(e.to))
      issues.push({ severity: "error", message: `Edge ${e.from} → ${e.to}: endpoint missing.` });
    if (seen.has(key)) { /* dedupe silently */ }
    seen.add(key);
    if (e.type === "control") {
      const src = byId.get(e.from);
      if (src && !isControlSource(src.primitive))
        issues.push({
          severity: "error",
          message: `Control edge ${e.from} → ${e.to}: only verdict nodes (critic/gate) may repeat.`,
        });
      if (e.max_passes !== undefined && e.max_passes < 1)
        issues.push({ severity: "error", message: `Control edge ${e.from} → ${e.to}: max_passes must be >= 1.` });
    }
  }
  return issues;
}
