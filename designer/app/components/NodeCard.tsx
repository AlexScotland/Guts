"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import NodePanel from "./NodePanel";
import styles from "./NodeCard.module.css";
import {
  NODE_W,
  inputTypes,
  isControlSource,
  outputTypes,
  rowTop,
  type CanvasNode,
  type CanvasGraph,
  type PrimitiveMeta,
} from "../lib/graph";
import type { BranchSpec } from "../lib/types";
import { type NodeOutputs, type NodeRunState } from "../lib/run";

interface Props {
  node: CanvasNode;
  graph: CanvasGraph;
  meta: Map<string, PrimitiveMeta>;
  selected: boolean;
  connectActive: boolean;
  /** Run state from the canvas seam (live or finished): null when the
   *  node never ran. "running" = the node is EXECUTING right now. */
  status?: NodeRunState | null;
  /** The `skipped` event's detail for a SKIPPED node (v1 routing: "not
   *  routed" / "upstream pruned"; on_failure=skip carries its own reason) —
   *  the pruned styling now carries its WHY (docs/ai-routing-nodes.md). */
  skipDetail?: string | null;
  /** Last-run outputs for this node (docs/ai-per-node-outputs.md); shown in
   *  the popover when there are entries. */
  outputs?: NodeOutputs | null;
  /** The record's top-level answer (the "graph answer" tag in the popup). */
  runAnswer?: string | null;
  onCardPointerDown: (e: React.PointerEvent, node: CanvasNode) => void;
  onOutPointerDown: (
    e: React.PointerEvent,
    node: CanvasNode,
    kind: "data" | "control"
  ) => void;
  onInPointerUp: (e: React.PointerEvent, node: CanvasNode) => void;
  onRemove: (id: string) => void;
  /** Edit callbacks (wired through from the page) for the node inspector. */
  onNode: (id: string, patch: Partial<CanvasNode>) => void;
  onBranches: (id: string, branches: BranchSpec[]) => void;
  /** Dismiss the node inspector (deselect). */
  onClose: () => void;
}

/** Popover width — the anchor math below needs a number close enough to the
 *  CSS size (min(440px, 92vw)). */
const POP_W = 440;
const POP_MAX_H = 620;

/** One graph node: typed input ports (left), typed output ports (right),
 *  a control (repeat) port for verdict nodes. Ports are drag handles; the
 *  header is the move handle.
 *
 *  Clicking the node (a clean click, not a drag) SELECTS it, and — via the
 *  `selected` prop — opens the on-node INSPECTOR POPOVER (portaled to
 *  <body>, escaping the canvas transform/zoom): the node's config + last
 *  output, in place, instead of a fixed right-hand panel. */
export default function NodeCard({
  node,
  graph,
  meta,
  selected,
  connectActive,
  status = null,
  skipDetail = null,
  outputs: lastOutputs = null,
  runAnswer = null,
  onCardPointerDown,
  onOutPointerDown,
  onInPointerUp,
  onRemove,
  onNode,
  onBranches,
  onClose,
}: Props) {
  const inputs = inputTypes(node.primitive, meta);
  const outputs = outputTypes(node.primitive, meta);
  const control = isControlSource(node.primitive);
  const rows = Math.max(inputs.length, outputs.length, 1);

  const rootRef = useRef<HTMLDivElement>(null);
  const [popAnchor, setPopAnchor] = useState<{
    left: number;
    top: number;
    flip: boolean;
  } | null>(null);

  // Anchor the popover in VIEWPORT coordinates from the card's live rect:
  // open to the RIGHT of the node, flip to the LEFT when it would run off-
  // screen. Recompute whenever it (re)opens so a moved node re-anchors.
  useEffect(() => {
    if (!selected) {
      setPopAnchor(null);
      return;
    }
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    const flip = rect.right + POP_W + 24 > window.innerWidth;
    const maxH = Math.min(window.innerHeight * 0.7, POP_MAX_H);
    setPopAnchor({
      left: flip ? rect.left - POP_W - 14 : rect.right + 14,
      top: Math.max(12, Math.min(rect.top, window.innerHeight - maxH - 12)),
      flip,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // Escape dismisses the inspector. (Clicking the canvas / another node /
  // an edge changes selection upstream and closes it; clicking inside the
  // popover is a different DOM subtree, so it stays open.)
  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, onClose]);

  const branchSummary =
    node.primitive === "parallel"
      ? `${node.branches.length} branch${node.branches.length === 1 ? "" : "es"}`
      : null;

  const statusClass = status ? styles[status] : "";

  // A skipped node carries its reason (v1 routing: "not routed" /
  // "upstream pruned") — the pruned styling now shows its WHY, not just
  // a dashed border.
  const skipLabel =
    status === "skipped"
      ? skipDetail
        ? `skipped — ${skipDetail}`
        : "skipped"
      : null;

  const nodeBody = (
    <div
      ref={rootRef}
      className={`${styles.node} ${selected ? styles.selected : ""} ${statusClass}`}
      style={{ left: node.x, top: node.y, width: NODE_W }}
      data-node={node.id}
      data-run-state={status ?? undefined}
      onPointerDown={(e) => onCardPointerDown(e, node)}
      onPointerUp={(e) => onInPointerUp(e, node)}
    >
      <header className={styles.head}>
        <span className={styles.primitive}>{node.primitive}</span>
        <span className={styles.id}>{node.id}</span>
        {status === "running" && (
          <span
            className={styles.stateDot}
            role="status"
            aria-label={`Node ${node.id} is running`}
            title={`Node ${node.id} is running`}
          />
        )}
        <button
          type="button"
          className={styles.remove}
          aria-label={`Remove node ${node.id}`}
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => onRemove(node.id)}
        >
          ×
        </button>
      </header>

      <div className={styles.body}>
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className={styles.row} style={{ top: rowTop(i) }}>
            <div className={styles.cellL}>
              {i < inputs.length && (
                <span
                  className={`${styles.port} ${styles.portIn} ${
                    connectActive ? styles.dropTarget : ""
                  }`}
                  style={{ top: 3 }}
                  title={`input: ${inputs[i]}`}
                  onPointerUp={(e) => onInPointerUp(e, node)}
                >
                  <span className={styles.dot} />
                  {inputs[i]}
                </span>
              )}
            </div>
            <div className={styles.cellR}>
              {i < outputs.length && (
                <span
                  className={`${styles.port} ${styles.portOut}`}
                  style={{ top: 3 }}
                  title={`output: ${outputs[i]}`}
                  onPointerDown={(e) =>
                    onOutPointerDown(e, node, "data")
                  }
                >
                  {outputs[i]}
                  <span className={styles.dot} />
                </span>
              )}
            </div>
          </div>
        ))}
        {control && (
          <div
            className={styles.row}
            style={{ top: rowTop(rows) + 10 }}
          >
            <div className={styles.cellL} />
            <div className={styles.cellR}>
              <span
                className={`${styles.port} ${styles.portCtl}`}
                style={{ top: 4 }}
                title="control edge: declared repeat (max passes)"
                onPointerDown={(e) =>
                  onOutPointerDown(e, node, "control")
                }
              >
                ⟲ repeat
                <span className={`${styles.dot} ${styles.dotCtl}`} />
              </span>
            </div>
          </div>
        )}
      </div>

      {branchSummary && (
        <footer className={styles.foot}>{branchSummary}</footer>
      )}
      {skipLabel && (
        <footer className={styles.skipFoot} title={skipDetail ?? "skipped"}>
          {skipLabel}
        </footer>
      )}
    </div>
  );

  return (
    <>
      {nodeBody}
      {selected &&
        popAnchor &&
        createPortal(
          <div
            role="dialog"
            aria-label={`Inspector for ${node.id}`}
            className={styles.pop}
            style={{
              left: Math.max(10, popAnchor.left),
              top: popAnchor.top,
            }}
            // CRITICAL: the portal is DOM-placed on <body>, but React
            // synthetic events follow the COMPONENT tree — a pointerdown in
            // this card would bubble to the canvas viewport's onPointerDown
            // (startPan), whose first line is onSelect(null) → the inspector
            // closed on every click inside it. Stop it here. Input focus,
            // text selection and the × button (onClick) are unaffected:
            // stopPropagation blocks React handlers, not default actions.
            onPointerDown={(e) => e.stopPropagation()}
          >
            <header className={styles.popHead}>
              <span className={styles.popTitle}>
                node · {node.id}
              </span>
              <span className={styles.popPrimitive}>{node.primitive}</span>
              <button
                type="button"
                className={styles.popClose}
                aria-label="Close inspector"
                onClick={onClose}
              >
                ×
              </button>
            </header>
            <div className={styles.popBody}>
              <NodePanel
                key={node.id}
                node={node}
                graph={graph}
                meta={meta}
                onNode={onNode}
                onBranches={onBranches}
                lastOutputs={lastOutputs}
                runAnswer={runAnswer}
              />
            </div>
          </div>,
          document.body
        )}
    </>
  );
}
