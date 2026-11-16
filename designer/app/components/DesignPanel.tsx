"use client";

import { useState } from "react";
import styles from "./DesignPanel.module.css";
import { designDraft, type DesignDraftResult } from "../lib/api";
import type { EdgeSpec, GraphDoc, NodeSpec } from "../lib/types";

interface Props {
  /** While false the panel renders nothing but STAYS MOUNTED — closing is a
   *  hide, not a tear-down: the author's ask, notes, checkbox, last draft,
   *  and local history must survive a close/reopen (unmounting would kill
   *  them — the panel lives in the page, hidden, not conditionally created). */
  open: boolean;
  /** The working canvas as a document — offered as the draft's `current`
   *  context (face 0 contract: stateless core, caller owns the context). */
  current: GraphDoc;
  /** Replace the whole canvas with a drafted document (face 2 load path). */
  onLoad: (doc: Record<string, unknown>) => void;
  onClose: () => void;
}

type Phase = "idle" | "working" | "done" | "failed";

/** A local, in-component history entry (face 2: history is LOCAL only —
 *  no server-side sessions; the design face stays stateless). */
interface HistoryEntry {
  at: string;
  ask: string;
  result: DesignDraftResult;
}

/** Both callers hand us a graph document: the live canvas (typed GraphDoc)
 *  or a draft straight off the wire (Record<string, unknown>). */
type DocLike = GraphDoc | Record<string, unknown>;

function docNodes(doc: DocLike): [string, NodeSpec][] {
  const nodes = (doc as Record<string, unknown>).nodes;
  if (!nodes || typeof nodes !== "object" || Array.isArray(nodes)) return [];
  return Object.entries(nodes as Record<string, NodeSpec>);
}

function docEdges(doc: DocLike): EdgeSpec[] {
  const edges = (doc as Record<string, unknown>).edges;
  return Array.isArray(edges) ? (edges as EdgeSpec[]) : [];
}

/** THE DESIGN DOOR (face 2): ask in sentence form, the GUTS design face
 *  (face 1 — the repo's MCP server) drafts a graph, the green door's
 *  verdict rides with it, and a one-click load puts the draft ON the
 *  canvas in front of the author. The designer is just a client here:
 *  all design intelligence lives in app/design/. */
export default function DesignPanel({ open, current, onLoad, onClose }: Props) {
  const [ask, setAsk] = useState(
    "I want a graph that does research, then does a fact check, drops irrelevant knowledge and loops until a decision that the original ask is completed"
  );
  const [notes, setNotes] = useState("");
  const [useCurrent, setUseCurrent] = useState(false);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [turn, setTurn] = useState<DesignDraftResult | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  // Closed = hidden, NOT unmounted: the hooks above have already run, so
  // every field (ask / notes / useCurrent / phase / turn / history) survives
  // the hide and is exactly as the author left it when the door reopens.
  if (!open) return null;

  const generate = async () => {
    if (!ask.trim()) return;
    setPhase("working");
    setError(null);
    try {
      const out = await designDraft(
        ask.trim(),
        notes,
        useCurrent ? (current as unknown as Record<string, unknown>) : undefined
      );
      setTurn(out);
      setHistory((h) =>
        [{ at: new Date().toISOString(), ask: ask.trim(), result: out }, ...h].slice(0, 8)
      );
      setPhase("done");
    } catch (e) {
      setPhase("failed");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const ok = turn?.verdict.ok;
  const nodes = turn ? docNodes(turn.doc) : [];
  const edges = turn ? docEdges(turn.doc) : [];

  return (
    <>
      <div className={styles.scrim} onClick={onClose} aria-hidden="true" />
      <aside className={styles.door} role="dialog" aria-label="AI design">
        <header className={styles.head}>
          <div>
            <h2 className={styles.title}>
              <span className={styles.spark} aria-hidden="true">✦</span> AI design
            </h2>
            <p className={styles.sub}>
              Your ask in → the GUTS design face drafts a graph out → load it onto the canvas.
            </p>
          </div>
          <button
            type="button"
            className={styles.close}
            onClick={onClose}
            aria-label="Close design panel"
          >
            ×
          </button>
        </header>

        <div className={styles.fields}>
          <label className={styles.field}>
            <span className={styles.fieldLabel}>Ask</span>
            <textarea
              className={styles.ask}
              value={ask}
              rows={4}
              onChange={(e) => setAsk(e.target.value)}
              placeholder="Describe what the loop should do, in one or two sentences."
            />
          </label>

          <label className={styles.field}>
            <span className={styles.fieldLabel}>
              Notes (optional) — business case, stop conditions, priorities
            </span>
            <textarea
              className={styles.ask}
              value={notes}
              rows={3}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. stop after ~5 passes; drop sources that are not about the ask; a verdict of “not completed” is a normal result, not an error."
            />
          </label>

          <label className={styles.ctxRow}>
            <input
              type="checkbox"
              checked={useCurrent}
              onChange={(e) => setUseCurrent(e.target.checked)}
            />
            <span>
              ground the draft in the current canvas{" "}
              <code className={styles.mono}>
                {docNodes(current).length} nodes
              </code>
            </span>
          </label>

          <div className={styles.actions}>
            <button
              type="button"
              className={styles.generate}
              onClick={() => void generate()}
              disabled={phase === "working" || !ask.trim()}
            >
              {phase === "working" ? "Drafting…" : turn ? "Draft again" : "Draft graph"}
            </button>
            {turn && phase !== "working" && (
              <button
                type="button"
                className={styles.load}
                onClick={() => onLoad(turn.doc)}
              >
                Load onto canvas →
              </button>
            )}
          </div>
          <p className={styles.hint}>
            {phase === "working"
              ? "One model turn — this can take a minute or two. Nothing is saved until you hit Load, then Save."
              : "Stateless: every draft starts from your ask, your notes, and (optionally) the canvas. A red verdict is a normal result, not a failure."}
          </p>
        </div>

        {phase === "failed" && error && (
          <div className={styles.error} role="alert">
            {error}
          </div>
        )}

        {turn && (
          <div className={styles.result}>
            <div className={styles.verdictRow}>
              <span
                className={`${styles.chip} ${ok ? styles.chipOk : styles.chipErr}`}
              >
                {ok ? "validate_graph ✓" : "validate_graph ✗"}
              </span>
              <span className={styles.shape}>
                {nodes.length} nodes · {edges.length} edges
              </span>
            </div>

            {!ok && turn.verdict.issues.length > 0 && (
              <ul className={styles.issues}>
                {turn.verdict.issues.map((issue, i) => (
                  <li key={i}>{issue}</li>
                ))}
              </ul>
            )}

            <ul className={styles.docList}>
              {nodes.map(([id, n]) => (
                <li key={id} className={styles.docRow}>
                  <code className={styles.mono}>{id}</code>
                  <span className={styles.prim}>{n.primitive}</span>
                  {n.config && Object.keys(n.config).length > 0 && (
                    <span className={styles.cfg}>
                      {Object.keys(n.config).join(", ")}
                    </span>
                  )}
                </li>
              ))}
              {edges.map((e, i) => (
                <li key={`${e.from}-${e.to}-${i}`} className={styles.docRow}>
                  <span className={styles.edge}>
                    {e.from} → {e.to}
                  </span>
                  <span className={styles.ctxRow2}>{e.type}</span>
                  {typeof e.max_passes === "number" && (
                    <span className={styles.cfg}>≤ {e.max_passes} passes</span>
                  )}
                </li>
              ))}
            </ul>

            <details className={styles.jsonWrap}>
              <summary>full document (JSON)</summary>
              <pre className={styles.json}>
                {JSON.stringify(turn.doc, null, 2)}
              </pre>
            </details>
          </div>
        )}

        {history.length > 1 && (
          <div className={styles.history}>
            <span className={styles.fieldLabel}>local history (this window only)</span>
            <ul className={styles.histList}>
              {history.slice(1).map((h, i) => (
                <li key={i}>
                  <button
                    type="button"
                    className={styles.histRow}
                    onClick={() => {
                      setAsk(h.ask);
                      setTurn(h.result);
                      setPhase("done");
                      setError(null);
                    }}
                    title="Show this draft again"
                  >
                    <span
                      className={
                        h.result.verdict.ok ? styles.chipOkDot : styles.chipErrDot
                      }
                    >
                      {h.result.verdict.ok ? "✓" : "✗"}
                    </span>
                    <span className={styles.histAsk}>{h.ask}</span>
                    <span className={styles.histAt}>
                      {h.at.slice(11, 16).replace("T", ":")}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </aside>
    </>
  );
}
