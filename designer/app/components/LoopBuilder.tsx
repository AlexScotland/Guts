"use client";

/**
 * LoopBuilder — the agent node OWNS its loop's authoring (v1 UX).
 *
 * The loop being built is the canvas's working graph — the SAME state the
 * big canvas renders (edits here show on the canvas immediately, and vice
 * versa). The document does NOT change: `toDoc()` still emits the frozen
 * v0 shape and the agent still points at it through `loop: {ref}` (ref XOR
 * alias, the backend stays the authority). The builder moves WHERE the
 * author works: inside the node — not next to a dashed wire in the
 * inspector.
 *
 * What it edits (all through the shared canvas callbacks — page.tsx owns
 * the state, so the builder is pure control surface):
 *   nodes : add a primitive · rename (draft + commit, the NodePanel idiom) · remove
 *   edges : add from→to (data | control) · remove · set the repeat's cap
 *
 * A control edge IS the loop in the graph (the retry): at most
 * `max_passes` re-runs from its target, legal only from a verdict node
 * (critic / gate) — the add-edge row enforces that rule (the backend
 * validator still stays the authority when reachable).
 */

import { useState } from "react";
import styles from "./LoopBuilder.module.css";
import { isControlSource, type CanvasEdge, type CanvasGraph } from "../lib/graph";

interface Props {
  /** The canvas's OWN working graph — the loop being built (live source). */
  graph: CanvasGraph;
  /** The primitive vocabulary for the "add node" select (API list, or the
   *  offline fallback table — the same source as the Palette). */
  primitives: string[];
  onAddNode: (primitive: string) => void;
  /** Rename a node (committed; edges re-point). The canvas keeps the id. */
  onRenameNode: (id: string, nextId: string) => void;
  onRemoveNode: (id: string) => void;
  onAddEdge: (from: string, to: string, type: "data" | "control") => void;
  /** Patch one edge in place (e.g. the control edge's max_passes). */
  onPatchEdge: (edge: CanvasEdge, patch: Partial<CanvasEdge>) => void;
  onRemoveEdge: (from: string, to: string, type: string) => void;
}

export default function LoopBuilder({
  graph,
  primitives,
  onAddNode,
  onRenameNode,
  onRemoveNode,
  onAddEdge,
  onPatchEdge,
  onRemoveEdge,
}: Props) {
  // ── add-node ─────────────────────────────────────────────────────────
  const [newPrimitive, setNewPrimitive] = useState(
    primitives.includes("prompt") ? "prompt" : primitives[0] ?? "research"
  );

  // ── rename: DRAFT + COMMIT (the NodePanel idiom — commit on Enter,
  //     cancel on Escape; the id is a node's identity, so it validates on
  //     commit, not per keystroke). One row at a time. ─────────────────
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameErr, setRenameErr] = useState<string | null>(null);

  const startRename = (nodeId: string) => {
    setRenaming(nodeId);
    setRenameValue(nodeId);
    setRenameErr(null);
  };

  /** Commit the pending rename; returns true when the row may close.
   *  Rejections (empty / duplicate) name the error and keep it visible. */
  const commitRename = (): boolean => {
    if (renaming === null) return true;
    const next = renameValue.replace(/\s/g, "");
    if (!next) {
      setRenameErr("id can't be empty");
      return false;
    }
    if (next === renaming) {
      setRenaming(null);
      return true;
    }
    if (graph.nodes.some((n) => n.id === next)) {
      setRenameErr(`a node named "${next}" already exists`);
      return false;
    }
    setRenameErr(null);
    onRenameNode(renaming, next);
    setRenaming(null);
    return true;
  };

  /** Escape: a pure cancel — the row reverts, the error goes with it. */
  const cancelRename = () => {
    setRenaming(null);
    setRenameErr(null);
  };

  // ── add-edge form ──────────────────────────────────────────────────────
  const [eFrom, setEFrom] = useState(graph.nodes[0]?.id ?? "");
  const [eTo, setETo] = useState(graph.nodes[1]?.id ?? graph.nodes[0]?.id ?? "");
  const [eType, setEType] = useState<"data" | "control">("data");
  const [edgeErr, setEdgeErr] = useState<string | null>(null);

  const fromIsVerdict = isControlSource(
    graph.nodes.find((n) => n.id === eFrom)?.primitive ?? ""
  );

  const submitEdge = () => {
    if (!eFrom || !eTo) {
      setEdgeErr("pick both ends (add nodes first)");
      return;
    }
    if (eFrom === eTo) {
      setEdgeErr("a node can't wire to itself");
      return;
    }
    onAddEdge(eFrom, eTo, eType);
    setEdgeErr(null);
  };

  const nodes = graph.nodes;
  const options = nodes.map((n) => n.id);

  return (
    <div className={styles.box}>
      <header className={styles.head}>
        <span className={styles.tag}>loop</span>
        <span className={styles.headline}>built inline — this canvas</span>
      </header>

      {/* ── nodes ── */}
      <section className={styles.section}>
        <h4 className={styles.sectionTitle}>
          nodes <em>{nodes.length}</em>
        </h4>
        {nodes.length === 0 && (
          <p className={styles.hint}>
            no nodes yet — add the first one below (the entry node needs no
            wire).
          </p>
        )}
        <div className={styles.rows}>
          {nodes.map((n) => {
            const isRenaming = renaming === n.id;
            return (
              <div key={n.id} className={styles.row}>
                {isRenaming ? (
                  <input
                    className={styles.rename}
                    value={renameValue}
                    autoFocus
                    spellCheck={false}
                    onChange={(e) => {
                      setRenameValue(e.target.value);
                      setRenameErr(null);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") commitRename();
                      if (e.key === "Escape") cancelRename();
                    }}
                    onBlur={() => {
                      /* Leaving by clicking elsewhere: a changed name commits,
                         a blank one cancels; a REJECTED name (duplicate)
                         closes the row but keeps the error line naming why. */
                      if (!commitRename()) setRenaming(null);
                    }}
                  />
                ) : (
                  <span className={styles.nodeId}>
                    <b>{n.id}</b>
                  </span>
                )}
                <i className={styles.primitive}>{n.primitive}</i>
                <span className={styles.rowActions}>
                  {!isRenaming && (
                    <button
                      type="button"
                      className={styles.mini}
                      title="Rename this node (Enter commits)"
                      onClick={() => startRename(n.id)}
                    >
                      ✎
                    </button>
                  )}
                  <button
                    type="button"
                    className={styles.miniDanger}
                    aria-label={`Remove node ${n.id}`}
                    title="Remove this node (and its wires)"
                    onClick={() => onRemoveNode(n.id)}
                  >
                    ×
                  </button>
                </span>
              </div>
            );
          })}
        </div>
        {renameErr && <p className={styles.err}>{renameErr}</p>}
        <div className={styles.addRow}>
          <select
            className={styles.grow}
            value={newPrimitive}
            onChange={(e) => setNewPrimitive(e.target.value)}
            title="The primitive to add (the same vocabulary as the left palette)"
          >
            {primitives.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <button
            type="button"
            className={styles.add}
            onClick={() => {
              onAddNode(newPrimitive);
            }}
          >
            + add node
          </button>
        </div>
      </section>

      {/* ── edges (the wiring — data flow, and the repeat) ── */}
      <section className={styles.section}>
        <h4 className={styles.sectionTitle}>
          edges <em>{graph.edges.length}</em>
        </h4>
        {nodes.length < 2 && (
          <p className={styles.hint}>
            need at least two nodes to wire — the entry node stands alone.
          </p>
        )}
        <div className={styles.rows}>
          {graph.edges.map((e) => (
            <div key={`${e.from}->${e.to}:${e.type}`} className={styles.row}>
              <span className={styles.edgeEnds}>
                <b>{e.from}</b>
                <span aria-hidden>→</span>
                <b>{e.to}</b>
              </span>
              {e.type === "control" ? (
                <span
                  className={styles.repeat}
                  title="the loop (retry): re-run from the target at most this many passes when the verdict doesn't pass"
                >
                  ≤
                  <input
                    className={styles.passes}
                    type="number"
                    min={1}
                    value={e.max_passes ?? 1}
                    onChange={(ev) => {
                      const v = Math.max(1, Number(ev.target.value) || 1);
                      onPatchEdge(e, { max_passes: v });
                    }}
                  />
                  pass(es)
                </span>
              ) : (
                <i className={styles.edgeType}>data</i>
              )}
              <span className={styles.rowActions}>
                <button
                  type="button"
                  className={styles.miniDanger}
                  aria-label={`Remove edge ${e.from} to ${e.to}`}
                  onClick={() => onRemoveEdge(e.from, e.to, e.type)}
                >
                  ×
                </button>
              </span>
            </div>
          ))}
          {graph.edges.length === 0 && (
            <p className={styles.hint}>no wires yet — the nodes don't pass data.</p>
          )}
        </div>
        <div className={styles.edgeFormRow}>
          <select
            className={styles.shrink}
            value={eFrom}
            onChange={(e) => {
              setEFrom(e.target.value);
              setEdgeErr(null);
            }}
          >
            {options.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
          <span aria-hidden>→</span>
          <select
            className={styles.shrink}
            value={eTo}
            onChange={(e) => {
              setETo(e.target.value);
              setEdgeErr(null);
            }}
          >
            {options.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
          <select
            className={styles.typeSelect}
            value={eType}
            onChange={(e) => {
              const t = e.target.value as "data" | "control";
              setEType(t);
              setEdgeErr(null);
            }}
          >
            <option value="data">data</option>
            <option
              value="control"
              disabled={!fromIsVerdict}
              title="only a verdict node (critic/gate) may drive the repeat"
            >
              ⟲ repeat
            </option>
          </select>
          <button
            type="button"
            className={styles.add}
            disabled={nodes.length < 2}
            onClick={submitEdge}
          >
            + wire
          </button>
        </div>
        {edgeErr && <p className={styles.err}>{edgeErr}</p>}
        {eType === "control" && fromIsVerdict && (
          <p className={styles.hint}>
            the repeat (loop) re-runs from the target node at most{" "}
            <code>max_passes</code> times when the verdict doesn't pass — set
            the pass count on the wire row above.
          </p>
        )}
        {eType === "control" && !fromIsVerdict && (
          <p className={styles.hint}>
            the ⟲ repeat is legal only OUT of a verdict node (critic / gate) —
            pick one as the wire's start.
          </p>
        )}
      </section>
    </div>
  );
}
