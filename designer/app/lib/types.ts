/**
 * Client types for the GUTS API (designer view).
 *
 * Mirrors the frozen v0 contract in docs/graph-loop-designer.md:
 * the closed 8-primitive vocabulary, typed ports, one entry, control
 * edges only from verdict nodes (critic/gate), bounded retries, inline
 * parallel branches. The backend (schemas/graph.py) is the authority —
 * these types just keep the canvas honest.
 */

export type PortType =
  | "context"
  | "goal"
  | "plan"
  | "result"
  | "verdict"
  | "merged"
  | "pass";

/** One primitive card from GET /api/components (`primitives` block). */
export interface PrimitiveInfo {
  name: string;
  version: string;
  description?: string;
  provides?: PortType[];
  accepts?: PortType[];
  requires?: PortType[];
  requires_any?: PortType[][];
  side_effects?: boolean;
  budgeted?: boolean;
}

export interface GraphMeta {
  id: string;
  name: string;
  version: string;
  node_count: number;
  active: boolean;
  updated_at?: number | null;
}

export interface GraphsStatus {
  active: string | null;
  graphs: GraphMeta[];
}

export interface ComponentsStatus {
  slots: Record<string, { active: string; available: { alias: string; version?: string; description?: string }[] }>;
  presets: Record<string, Record<string, string>>;
  primitives: PrimitiveInfo[];
  graphs?: GraphsStatus;
}

export interface BranchGraph {
  name?: string;
  nodes: Record<string, BranchNodeSpec>;
  edges: EdgeSpec[];
}

export interface BranchNodeSpec {
  primitive: string;
  config?: Record<string, unknown>;
  budget?: number | null;
  on_failure?: string;
}

export interface BranchSpec {
  name: string;
  budget?: number | null;
  graph: BranchGraph;
}

export interface NodeSpec {
  primitive: string;
  config?: Record<string, unknown>;
  budget?: number | null;
  on_failure?: string;
  /** parallel nodes only: inline branch graphs (1+). */
  branches?: BranchSpec[];
}

export interface EdgeSpec {
  from: string;
  to: string;
  type: "data" | "control";
  /** control edges only: required, >= 1. */
  max_passes?: number;
}

export interface GraphDoc {
  name?: string;
  version?: string;
  budget?: number | null;
  max_parallel?: number | null;
  nodes: Record<string, NodeSpec>;
  edges: EdgeSpec[];
}

/** One node event from a run (start/done/result/failure/skipped/retry/control).

 *  `result` events additionally carry the node's actual outputs
 *  (docs/ai-per-node-outputs.md): `outputs` is a clean per-type map;
 *  `truncated` (only when cut) maps output key -> original char length.
 */
export interface RunEvent {
  event: string;
  node?: string;
  branch?: string;
  detail?: string;
  outputs?: Record<string, unknown>;
  truncated?: Record<string, number>;
  [key: string]: unknown;
}

export interface LastRun {
  /** "running" = the LIVE record (run in flight; events are partial) —
   *  the same endpoint serves both while the designer polls. */
  status: "ok" | "failed" | "running" | string;
  at: number;
  answer?: string | null;
  error?: string | null;
  events: RunEvent[];
}

/* ── Agent graphs (capability-composition docs, backend /api/agents) ──
 *  The designer renders the LAYER ABOVE (docs/agent-graph.md): one line per
 *  agent + the bundle's shape + the loop reference expandable into the
 *  referenced turn-graph. The backend is the authority for validation. */

export interface AgentMeta {
  id: string;
  name: string;
  active: boolean;
  /** loop: a saved graph id (ref) or a built-in loop alias. */
  loop?: string;
  model?: string | null;
  mcp: string[];
  skills: string[];
  has_prompt: boolean;
  settings: Record<string, string>;
}

export interface AgentsStatus {
  active: string | null;
  agents: AgentMeta[];
}

export interface AgentMcpEntry {
  alias: string;
  transport?: string;
  url?: string;
  command?: string[];
}

export interface AgentDocument {
  name?: string;
  prompt?: string;
  model?: { provider?: string; id?: string };
  mcp?: AgentMcpEntry[];
  skill?: { path: string; tools?: string[] }[];
  settings?: Record<string, string>;
  loop?: { ref?: string; alias?: string };
}

export interface AgentDetail {
  id: string;
  name: string;
  agent: AgentDocument;
}

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
