"use client";

import styles from "./Inspector.module.css";
import type {
  CanvasEdge,
  CanvasGraph,
  CheckIssue,
} from "../lib/graph";
import type { Selection } from "./Canvas";

/**
 * The slim right rail. Node details (config, id, budget, on_failure,
 * branches) and the per-node last output moved to the ON-NODE inspector
 * popover (NodeCard/NodePanel). What's left here is genuinely global:
 * graph-level settings + the structural checks, plus the edge panel
 * (edges aren't tied to a node, so they keep a persistent surface).
 */

interface Props {
  graph: CanvasGraph;
  selection: Selection | null;
  issues: CheckIssue[];
  onGraph: (patch: Partial<Pick<CanvasGraph, "name" | "budget" | "max_parallel">>) => void;
  onEdge: (edge: CanvasEdge, patch: Partial<CanvasEdge>) => void;
}

export default function Inspector({
  graph,
  selection,
  issues,
  onGraph,
  onEdge,
}: Props) {
  const edge =
    selection?.kind === "edge"
      ? graph.edges.find(
          (e) => e.from + "->" + e.to + ":" + e.type === selection.key
        )
      : undefined;

  return (
    <aside className={styles.inspector}>
      <h1 className={styles.railTitle}>Graph</h1>

      <section className={styles.block}>
        <h2>Settings</h2>
        <label className={styles.field}>
          <span>Name</span>
          <input
            value={graph.name}
            onChange={(e) => onGraph({ name: e.target.value })}
            placeholder="my-loop"
          />
        </label>
        <div className={styles.pair}>
          <label className={styles.field}>
            <span>Budget</span>
            <input
              type="number"
              min={0}
              placeholder="∞"
              value={graph.budget ?? ""}
              onChange={(e) =>
                onGraph({
                  budget: e.target.value === "" ? null : Number(e.target.value),
                })
              }
            />
          </label>
          <label className={styles.field}>
            <span>max_parallel</span>
            <input
              type="number"
              min={1}
              placeholder="4"
              value={graph.max_parallel ?? ""}
              onChange={(e) =>
                onGraph({
                  max_parallel:
                    e.target.value === "" ? null : Number(e.target.value),
                })
              }
            />
          </label>
        </div>
        <p className={styles.hint}>
          Budgets are ceilings in executor units — the graph ceiling is
          binding. Empty = no ceiling (default parallel = 4).
        </p>
      </section>

      {edge && (
        <EdgePanel
          key={edge.from + edge.to + edge.type}
          edge={edge}
          onEdge={onEdge}
        />
      )}

      {selection?.kind !== "edge" && (
        <p className={styles.railHint}>
          Select an edge to edit it, or click a node on the canvas to open its
          inspector.
        </p>
      )}

      <section className={styles.block}>
        <h2>Checks</h2>
        {issues.length === 0 ? (
          <p className={styles.ok}>
            Structural checks pass. The server validator is the final word
            (it checks typable edges, entry, cycles, branch rules).
          </p>
        ) : (
          <ul className={styles.issues}>
            {issues.map((i, k) => (
              <li
                key={k}
                className={
                  i.severity === "error" ? styles.issueErr : styles.issueWarn
                }
              >
                {i.message}
              </li>
            ))}
          </ul>
        )}
      </section>
    </aside>
  );
}

// ── edge panel ────────────────────────────────────────────────────────────

function EdgePanel({
  edge,
  onEdge,
}: {
  edge: CanvasEdge;
  onEdge: (edge: CanvasEdge, patch: Partial<CanvasEdge>) => void;
}) {
  return (
    <section className={styles.block}>
      <h2>
        Edge <code>{edge.from} → {edge.to}</code>
      </h2>
      <div className={styles.meta}>
        <span className={styles.pill}>{edge.type}</span>
      </div>
      {edge.type === "control" && (
        <label className={styles.field}>
          <span>max_passes (declared repeat ceiling)</span>
          <input
            type="number"
            min={1}
            value={edge.max_passes ?? 2}
            onChange={(e) =>
              onEdge(edge, {
                max_passes: Math.max(1, Number(e.target.value) || 1),
              })
            }
          />
        </label>
      )}
      <p className={styles.hint}>
        Control edges repeat the downstream path; data edges are the acyclic
        backbone.
      </p>
    </section>
  );
}
