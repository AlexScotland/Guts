"use client";

import { useState } from "react";
import styles from "./TestPanel.module.css";
import { runningNodes, truncateLabel, type NodeRunState } from "../lib/run";
import type { LastRun } from "../lib/types";

interface Props {
  online: boolean;
  graphId: string;
  dirty: boolean;
  onRun: (goal: string) => Promise<{ run: LastRun; note?: string }>;
  /** Per-node run states (the canvas seam) — live while a run is in flight. */
  nodeStates?: Map<string, NodeRunState> | null;
}

type Phase = "idle" | "running" | "done" | "failed";

/** The test bench: one goal in, the graph's answer + node-event stream out
 *  (frozen v0 rule: runs are evidence, never assertions). */
export default function TestPanel({ online, graphId, dirty, onRun, nodeStates = null }: Props) {
  const [goal, setGoal] = useState(
    "Explain how this harness hot-swaps components, in 3 bullets citing the slot names."
  );
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [run, setRun] = useState<LastRun | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const runIt = async () => {
    setPhase("running");
    setError(null);
    setRun(null);
    setNote(null);
    try {
      const out = await onRun(goal);
      setRun(out.run);
      setNote(out.note ?? null);
      if (out.run.status === "ok") setPhase("done");
      else {
        setPhase("failed");
        setError(out.run.error ?? `run finished with status ${out.run.status}`);
      }
    } catch (e) {
      setPhase("failed");
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <section className={styles.panel} aria-label="Test run">
      <header className={styles.head}>
        <h2 className={styles.title}>
          Run <span className={styles.graphId}>{graphId}</span>
        </h2>
        {phase === "done" && (
          <span className={`${styles.chip} ${styles.chipOk}`}>
            {run?.status === "ok" ? "ok" : run?.status ?? "done"}
          </span>
        )}
        {phase === "failed" && (
          <span className={`${styles.chip} ${styles.chipErr}`}>failed</span>
        )}
      </header>

      <label className={styles.goal}>
        <span>Goal (sent as the chat message)</span>
        <textarea
          rows={3}
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
        />
      </label>

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.run}
          onClick={runIt}
          disabled={!online || phase === "running" || goal.trim().length === 0}
        >
          {phase === "running"
            ? "Running…"
            : dirty
              ? "Save + activate + run"
              : "Activate + run"}
        </button>
        <span className={styles.hint}>
          Runs the graph on the backend — real LLM, evidence only.
        </span>
      </div>

      {phase === "running" && (
        <p className={styles.note} role="status">
          currently running:{" "}
          <span className={styles.evNode}>
            {(() => {
              const names = runningNodes(nodeStates);
              return names.length ? names.join(", ") : "…";
            })()}
          </span>
        </p>
      )}

      {note && <p className={styles.note}>{note}</p>}
      {error && <p className={styles.error}>{error}</p>}

      {run && phase !== "running" ? (
        <div className={styles.results}>
          {run?.answer && (
            <>
              <h3 className={styles.sub}>Answer</h3>
              <pre className={styles.answer}>{run.answer}</pre>
            </>
          )}
          {run?.error && (
            <>
              <h3 className={styles.sub}>Engine error</h3>
              <pre className={styles.answer}>{run.error}</pre>
            </>
          )}
          {run && run.events.length > 0 && (
            <>
              <h3 className={styles.sub}>
                Node events ({run.events.length})
              </h3>
              <ul className={styles.events}>
                {run.events.map((ev, i) => (
                  <li key={i} className={styles.event}>
                    <span className={styles.evKind}>{ev.event}</span>
                    {ev.node && <span className={styles.evNode}>{ev.node}</span>}
                    {ev.branch && <span className={styles.evBranch}>{ev.branch}</span>}
                    {ev.detail && <span className={styles.evDetail}>{String(ev.detail).slice(0, 120)}</span>}
                    {/* result rows expand to the node's actual outputs
                        (docs/ai-per-node-outputs.md); branch badge above labels it */}
                    {ev.event === "result" && ev.outputs ? (
                      <details className={styles.evOutputs}>
                        <summary>outputs</summary>
                        {Object.entries(ev.outputs).map(([k, v]) => (
                          <div key={k} className={styles.evOutRow}>
                            <span className={styles.evOutKey}>{k}</span>
                            {ev.truncated?.[k] != null && (
                              <span className={styles.evTrunc}>{truncateLabel(ev.truncated![k])}</span>
                            )}
                            <span className={styles.evOutVal}>
                              {typeof v === "string" ? v : JSON.stringify(v)}
                            </span>
                          </div>
                        ))}
                      </details>
                    ) : null}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}
