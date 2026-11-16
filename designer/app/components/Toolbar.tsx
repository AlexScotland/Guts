"use client";

import styles from "./Toolbar.module.css";

interface Props {
  id: string;
  dirty: boolean;
  saving: boolean;
  active: boolean;
  online: boolean;
  onSave: () => void;
  onActivate: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  /** Face 2: open the AI design panel (the GUTS MCP design face drafts a
   *  graph from your ask; load it onto the canvas from there). */
  onOpenDesign: () => void;
}

/** The working-graph control strip: identity, save, activate, lifecycle. */
export default function Toolbar({
  id,
  dirty,
  saving,
  active,
  online,
  onSave,
  onActivate,
  onDuplicate,
  onDelete,
  onOpenDesign,
}: Props) {
  return (
    <div className={styles.toolbar}>
      <div className={styles.left}>
        <code className={styles.id}>{id}</code>
        <span
          className={`${styles.badge} ${active ? styles.badgeActive : ""}`}
          title={
            active
              ? "This graph is the active workflow for POST /api/chat"
              : "Saved graphs run when activated: {loop: graph, graph_id}"
          }
        >
          {active ? "active" : "saved"}
        </span>
        {dirty && <span className={styles.dirty}>unsaved</span>}
      </div>

      <div className={styles.right}>
        <button
          type="button"
          className={`${styles.btn} ${styles.design}`}
          onClick={onOpenDesign}
          title="Ask the AI design face to draft a graph from your ask (GUTS MCP design face) — load the result onto the canvas"
        >
          ✦ Design
        </button>
        <button
          type="button"
          className={styles.btn}
          onClick={onDuplicate}
          disabled={!online}
          title="Save a copy under a new id (the store's open question: new id, never in-place)"
        >
          Duplicate
        </button>
        <button
          type="button"
          className={styles.btn}
          onClick={onDelete}
          disabled={!online}
        >
          Delete
        </button>
        <button
          type="button"
          className={styles.btn}
          onClick={onActivate}
          disabled={!online}
          title="POST /api/components/activate {loop: graph, graph_id} — takes effect on the next chat turn"
        >
          Activate
        </button>
        <button
          type="button"
          className={`${styles.btn} ${styles.primary}`}
          onClick={onSave}
          disabled={!online || saving}
        >
          {saving ? "Saving…" : dirty ? "Save changes" : "Save"}
        </button>
      </div>
    </div>
  );
}
