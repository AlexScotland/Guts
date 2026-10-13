/**
 * Live-run derivation (the canvas half of the API + canvas seam).
 *
 * The backend's event vocabulary (components/graph_loop.py, frozen v0
 * contract: observability) is the source of truth; here we fold a prefix or
 * the full event list into per-node states so the canvas can highlight the
 * CURRENT node while a run is in flight (status "running" on /last-run)
 * and show the finished outcome afterwards.
 *
 * Event → state map (engine order per node):
 *   start → running      done → ok
 *   failure → failed     retry → running (attempt resumes)
 *   skipped → skipped    aborted → failed (fail-fast)
 *   result / control are informational (no state change): `result` is
 *   followed immediately by `done` on the same node, `control` is an
 *   edge-level event (named for its verdict source, already `ok`).
 *
 * Folding in order is what makes re-runs read correctly: a control-edge
 * re-run emits start again, which flips the node back to `running`.
 * Parallel branches simply leave several nodes at `running` at once —
 * which is exactly what the canvas should highlight.
 */
import type { RunEvent } from "./types";

export type NodeRunState = "running" | "ok" | "failed" | "skipped";

const STATE_BY_EVENT: Record<string, NodeRunState> = {
  start: "running",
  done: "ok",
  failure: "failed",
  retry: "running",
  skipped: "skipped",
  aborted: "failed",
};

/** Per-node run state derived from (possibly partial) run events. */
export function deriveNodeStates(events: RunEvent[] | null | undefined): Map<string, NodeRunState> {
  const states = new Map<string, NodeRunState>();
  if (!events) return states;
  for (const ev of events) {
    if (!ev || typeof ev !== "object" || !ev.node) continue;
    const state = STATE_BY_EVENT[ev.event];
    if (state) states.set(ev.node, state);
  }
  return states;
}

/** The `skipped` event's `detail` per node (v1 routing vocabulary:
 *  "not routed" / "upstream pruned"; on_failure=skip carries its own
 *  reason). Last `skipped` per node wins; nodes skipped without a detail
 *  fall back to "skipped". Kept separate from deriveNodeStates so the
 *  state fold stays untouched by the frozen v0 contract. */
export function deriveNodeSkipDetails(
  events: RunEvent[] | null | undefined
): Map<string, string> {
  const out = new Map<string, string>();
  if (!events) return out;
  for (const ev of events) {
    if (!ev || typeof ev !== "object" || ev.event !== "skipped" || !ev.node) continue;
    out.set(ev.node, typeof ev.detail === "string" && ev.detail ? ev.detail : "skipped");
  }
  return out;
}

/** The node ids currently executing (for the canvas highlight). */
export function runningNodes(states: Map<string, NodeRunState> | null | undefined): string[] {
  if (!states) return [];
  return [...states.entries()]
    .filter(([, s]) => s === "running")
    .map(([id]) => id)
    .sort();
}

/** The `result` event's payload for one node, folded for click-to-read
 *  (docs/ai-per-node-outputs.md). Keyed on the BARE node id — the SAME id
 *  space as deriveNodeStates, so the two folds agree on the same event
 *  stream; `branch` is reported (for display), never used as a key.
 *  Last `result` per node wins (control-edge re-runs overwrite, mirroring
 *  the blackboard's "most recently executed upstream provider" rule). */
export interface NodeOutputs {
  outputs: Record<string, unknown>;
  /** key -> original char length; present only where the engine cut. */
  truncated?: Record<string, number>;
  /** branch tag of the latest contributing event (display only). */
  branch?: string;
}

/** Human label for a truncated output's ORIGINAL size. */
export function truncateLabel(n: number): string {
  return n >= 1024 ? `${Math.round(n / 1024)} KB total` : `${n} chars total`;
}

export function deriveNodeOutputs(events: RunEvent[] | null | undefined): Map<string, NodeOutputs> {
  const out = new Map<string, NodeOutputs>();
  if (!events) return out;
  for (const ev of events) {
    if (
      !ev ||
      typeof ev !== "object" ||
      ev.event !== "result" ||
      !ev.node ||
      !ev.outputs ||
      typeof ev.outputs !== "object"
    )
      continue;
    out.set(ev.node, {
      outputs: ev.outputs,
      truncated:
        ev.truncated && typeof ev.truncated === "object" ? ev.truncated : undefined,
      branch: typeof ev.branch === "string" ? ev.branch : undefined,
    });
  }
  return out;
}
