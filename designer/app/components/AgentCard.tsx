"use client";

/**
 * The canvas AGENT NODE (docs/agent-graph.md — "The agent node on the
 * canvas"): the designer's rendering of ONE agent document — the capability
 * bundle around exactly one `loop` child. It is a DESIGN-layer card: no
 * dataflow ports (the agent produces/consumes no goal/context/plan/result),
 * no wire endpoints; its one child is SHOWN on the card as a loop preview
 * (a mini render of the loop's nodes — LIVE when the ref names the canvas's
 * own graph, the fetched saved document otherwise — plus "open in canvas"),
 * because the wire agent → loop is a reference, never an in-canvas edge.
 * Selecting it opens the on-node inspector (AgentPanel) — the authoring
 * surface over the POST /api/agents door.
 */

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import AgentPanel from "./AgentPanel";
import LoopPreview from "./LoopPreview";
import styles from "./AgentCard.module.css";
import type { AgentDraft, CanvasAgent } from "../lib/agentNode";
import type { CanvasEdge, CanvasGraph } from "../lib/graph";

interface Props {
  agent: CanvasAgent;
  selected: boolean;
  /** This agent is the store's ACTIVE agent (the bind in effect). */
  active: boolean;
  /** The draft differs from the last saved document (unsaved changes). */
  dirty: boolean;
  /** Saved turn-graph ids — the loop `ref` slot's option list. */
  savedGraphIds: string[];
  /** The current canvas's turn-graph id (the natural first ref option). */
  graphId: string;
  /** The canvas's OWN working graph — the loop preview's LIVE source when
   *  the draft's loop ref names this very graph (no fetch, updates as the
   *  loop is drawn). */
  canvasGraph: CanvasGraph;
  /** The primitive vocabulary for the builder's "add node" select (the
   *  same source the Palette draws from). */
  primitives: string[];
  /** Bridge to the building surface: load the loop's saved graph onto the
   *  canvas (the library's "open in canvas"). */
  onOpenLoop: (graphId: string) => void;
  /** Loop-builder callbacks (the inspector's inline builder edits the
   *  canvas's shared state; page.tsx owns the mutations). */
  onAddNode: (primitive: string) => void;
  onRenameNode: (id: string, nextId: string) => void;
  onRemoveNode: (id: string) => void;
  onAddEdge: (from: string, to: string, type: "data" | "control") => void;
  onPatchEdge: (edge: CanvasEdge, patch: Partial<CanvasEdge>) => void;
  onRemoveEdge: (from: string, to: string, type: string) => void;
  /** The other canvas agent ids (rename collision check). */
  otherAgentIds: string[];
  /** "saving…" / "activating…" / "testing…" while an action is in flight. */
  busyLabel: string | null;
  onCardPointerDown: (e: React.PointerEvent, agent: CanvasAgent) => void;
  /** Remove the node from the canvas (the saved document is untouched —
   *  deleting that is an explicit action in the panel). */
  onRemove: (id: string) => void;
  onDraft: (id: string, patch: Partial<AgentDraft>) => void;
  /** Rename the node's identity (its saved-agent REST id). Returns an error
   *  message, or null when committed. */
  onRename: (oldId: string, nextId: string) => string | null;
  onSave: (id: string) => void;
  onActivate: (id: string) => void;
  /** The deactivation door is global (no id) — shown on the ACTIVE agent's
   *  inspector only, like the chat panel's Deactivate button. */
  onDeactivate: () => void;
  onDeleteSaved: (id: string) => void;
  onTestMcp: (id: string, alias: string) => void;
  onClose: () => void;
}

/** Popover width — the anchor math needs a number close to the CSS size. */
const POP_W = 460;
const POP_MAX_H = 700;

export default function AgentCard({
  agent,
  selected,
  active,
  dirty,
  savedGraphIds,
  graphId,
  canvasGraph,
  primitives,
  onOpenLoop,
  onAddNode,
  onRenameNode,
  onRemoveNode,
  onAddEdge,
  onPatchEdge,
  onRemoveEdge,
  otherAgentIds,
  busyLabel,
  onCardPointerDown,
  onRemove,
  onDraft,
  onRename,
  onSave,
  onActivate,
  onDeactivate,
  onDeleteSaved,
  onTestMcp,
  onClose,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [popAnchor, setPopAnchor] = useState<{
    left: number;
    top: number;
    flip: boolean;
  } | null>(null);

  // Anchor the inspector in VIEWPORT coordinates from the card's live rect
  // (open right, flip left near the screen edge) — the same seam NodeCard
  // uses, so the two node kinds behave identically.
  useEffect(() => {
    if (!selected) {
      setPopAnchor(null);
      return;
    }
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    const flip = rect.right + POP_W + 24 > window.innerWidth;
    const maxH = Math.min(window.innerHeight * 0.75, POP_MAX_H);
    setPopAnchor({
      left: flip ? rect.left - POP_W - 14 : rect.right + 14,
      top: Math.max(12, Math.min(rect.top + 24, window.innerHeight - maxH - 12)),
      flip,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // Escape dismisses the inspector (NodeCard's same behaviour).
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

  const { draft } = agent;
  const chips: string[] = [];
  if (draft.model?.id) chips.push(`model ${draft.model.id}`);
  for (const m of draft.mcp) if (m.alias.trim()) chips.push(`mcp ${m.alias.trim()}`);
  if (draft.skill.length > 0) chips.push(`${draft.skill.length} skill${draft.skill.length > 1 ? "s" : ""}`);
  if (draft.prompt.trim()) chips.push("persona");
  if (draft.settings.length > 0) chips.push(`${draft.settings.length} setting${draft.settings.length > 1 ? "s" : ""}`);

  const card = (
    <div
      ref={rootRef}
      className={`${styles.card} ${selected ? styles.selected : ""}`}
      style={{ left: agent.x, top: agent.y, width: 240 }}
      data-agent={agent.id}
      onPointerDown={(e) => onCardPointerDown(e, agent)}
    >
      <header className={styles.head}>
        <span className={styles.badge}>agent</span>
        <span className={styles.name} title={draft.name || "unnamed agent"}>
          {draft.name.trim() || agent.id}
        </span>
        <span className={styles.id}>{agent.id}</span>
        {active && (
          <span className={styles.active} title="active agent" aria-label="active agent">
            ●
          </span>
        )}
        <button
          type="button"
          className={styles.remove}
          aria-label={`Remove agent node ${agent.id}`}
          title="Remove from the canvas (the saved document is untouched)"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={() => onRemove(agent.id)}
        >
          ×
        </button>
      </header>

      <div className={styles.body}>
        {/* The one child that runs — SHOWN, not just linked (docs/agent-graph.md
            §loop preview): a mini render of the loop, live when the ref names
            the canvas's own graph (the canvas is where loops are built; this
            is the agent's border around them), the fetched saved document
            otherwise. The document stays a reference — shareable, testable,
            independent — the card just stops reading like a label. */}
        <LoopPreview
          loop={draft.loop}
          canvasGraph={canvasGraph}
          canvasGraphId={graphId}
          onOpenLoop={onOpenLoop}
        />
        {draft.loop.mode === "ref" &&
          graphId.trim() !== "" &&
          draft.loop.ref.trim() === graphId.trim() && (
            <p className={styles.loopNote}>
              this node owns its loop — select it to shape it inline (nodes,
              wires, the repeat) in its inspector
            </p>
          )}
        {chips.length > 0 && (
          <div className={styles.chips}>
            {chips.map((c) => (
              <span key={c} className={styles.chip}>
                {c}
              </span>
            ))}
          </div>
        )}
        <footer className={dirty ? `${styles.foot} ${styles.dirty}` : styles.foot}>
          {dirty ? "unsaved changes" : "in sync with the saved document"}
        </footer>
      </div>
    </div>
  );

  return (
    <>
      {card}
      {selected &&
        popAnchor &&
        createPortal(
          <div
            role="dialog"
            aria-label={`Agent inspector for ${agent.id}`}
            className={styles.pop}
            style={{
              left: Math.max(10, popAnchor.left),
              top: popAnchor.top,
            }}
            // Same subtlety as NodeCard: the portal DOM is on <body>, but
            // React events follow the component tree — a pointerdown inside
            // this dialog would bubble to the canvas viewport's onPointerDown
            // (startPan) and dismiss the inspector. Stop it here.
            onPointerDown={(e) => e.stopPropagation()}
          >
            <header className={styles.popHead}>
              <span className={styles.popTitle}>agent · {agent.id}</span>
              <span className={styles.popBadge}>layer above</span>
              <button
                type="button"
                className={styles.popClose}
                aria-label="Close agent inspector"
                onClick={onClose}
              >
                ×
              </button>
            </header>
            <div className={styles.popBody}>
              <AgentPanel
                key={agent.id}
                agent={agent}
                active={active}
                savedGraphIds={savedGraphIds}
                graphId={graphId}
                canvasGraph={canvasGraph}
                primitives={primitives}
                onOpenLoop={onOpenLoop}
                onAddNode={onAddNode}
                onRenameNode={onRenameNode}
                onRemoveNode={onRemoveNode}
                onAddEdge={onAddEdge}
                onPatchEdge={onPatchEdge}
                onRemoveEdge={onRemoveEdge}
                otherAgentIds={otherAgentIds}
                busyLabel={busyLabel}
                onDraft={onDraft}
                onRename={onRename}
                onSave={onSave}
                onActivate={onActivate}
                onDeactivate={onDeactivate}
                onDeleteSaved={onDeleteSaved}
                onTestMcp={onTestMcp}
              />
            </div>
          </div>,
          document.body
        )}
    </>
  );
}
