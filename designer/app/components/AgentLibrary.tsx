"use client";

import { useCallback, useState } from "react";
import styles from "./AgentLibrary.module.css";
import { getAgent, getGraph } from "../lib/api";
import {
  describeBundle,
  describeLoopGraph,
  summarizeAgent,
} from "../lib/agents";
import type {
  AgentDetail,
  AgentMeta,
  GraphDoc,
} from "../lib/types";

interface Props {
  /** null = the backend door is not live (offline). */
  agents: AgentMeta[] | null;
  activeId: string | null;
  online: boolean;
  /** Open the agent's loop ref (a saved turn-graph) in the canvas. */
  onOpenLoop: (graphId: string) => void;
  /** Author the agent: add a fresh agent node to the canvas
   *  (docs/agent-graph.md — the authoring surface). Optional so the
   *  read-only panel stays usable standalone. */
  onNewAgent?: () => void;
  /** Load a saved agent onto the canvas as an editable agent node. */
  onEditOnCanvas?: (id: string) => void;
}

type LoopView =
  | { state: "loading" }
  | { state: "ok"; name: string; nodes: { id: string; primitive: string }[]; edges: number }
  | { state: "fail"; message: string };

/** The saved agent documents (docs/agent-graph.md — the layer above the
 *  turn-graph): one line per agent, the bundle's shape when expanded, and
 *  the `loop` reference expandable into the referenced turn-graph.
 *  Read-only here — binding happens through the chat panel (bind-then-run). */
export default function AgentLibrary({
  agents,
  activeId,
  online,
  onOpenLoop,
  onNewAgent,
  onEditOnCanvas,
}: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [bundles, setBundles] = useState<Record<string, AgentDetail["agent"] | undefined>>({});
  const [bundleErrors, setBundleErrors] = useState<Record<string, string>>({});
  const [loopViews, setLoopViews] = useState<Record<string, LoopView>>({});

  const toggleBundle = useCallback(async (a: AgentMeta) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(a.id)) {
        next.delete(a.id);
      } else {
        next.add(a.id);
        // fetch the full document on demand (the list meta is the shape only)
        if (bundles[a.id] === undefined) {
          getAgent(a.id)
            .then((detail) => setBundles((b) => ({ ...b, [a.id]: detail.agent })))
            .catch((err) =>
              setBundleErrors((e) => ({
                ...e,
                [a.id]: err instanceof Error ? err.message : String(err),
              }))
            );
        }
      }
      return next;
    });
  }, [bundles]);

  const toggleLoop = useCallback(
    async (a: AgentMeta, doc: AgentDetail["agent"]) => {
      const ref = doc.loop?.ref;
      if (!ref) return; // the alias loop is a built-in — nothing to expand
      const key = `${a.id}:${ref}`;
      if (loopViews[key]) {
        // collapse: drop the entry (a key set to undefined won't type-check a
        // `Record<string, LoopView>` under strict), rather than null it out
        setLoopViews((v) => {
          const next = { ...v };
          delete next[key];
          return next;
        });
        return;
      }
      setLoopViews((v) => ({ ...v, [key]: { state: "loading" } }));
      try {
        const { graph } = await getGraph(ref);
        const view = describeLoopGraph(graph);
        setLoopViews((v) => ({
          ...v,
          [key]: {
            state: "ok",
            name: (graph as GraphDoc & { name?: string }).name ?? ref,
            nodes: view.nodes,
            edges: view.edges,
          },
        }));
      } catch (err) {
        setLoopViews((v) => ({
          ...v,
          [key]: { state: "fail", message: err instanceof Error ? err.message : String(err) },
        }));
      }
    },
    [loopViews]
  );

  return (
    <section className={styles.library} aria-label="Saved agents">
      <header className={styles.head}>
        <h2 className={styles.title}>Agents</h2>
        <span className={styles.hint} title="Persona + model + MCP + skills + exactly one loop child, bound to the context at turn start (bind-then-run).">
          layer above
        </span>
        {onNewAgent && (
          <button
            type="button"
            className={styles.add}
            onClick={onNewAgent}
            title="Add an agent node to the canvas — persona, model, mcp, skills, settings, around one loop (a saved graph ref or a built-in alias). Save/Activate/Delete/Test-MCP run over the REST door."
          >
            + new
          </button>
        )}
      </header>
      <ul className={styles.list}>
        {agents === null ? (
          !online && (
            <li className={styles.note}>backend offline — nothing loaded</li>
          )
        ) : agents.length === 0 ? (
          <li className={styles.note}>
            No saved agents yet — the built-in persona is in effect.
          </li>
        ) : (
          agents.map((a) => {
            const bundle = bundles[a.id];
            const b = bundle ? describeBundle(bundle, a.id) : undefined;
            const loopView = bundle?.loop?.ref ? loopViews[`${a.id}:${bundle.loop.ref}`] : undefined;
            return (
              <li key={a.id} className={styles.entry}>
                <button
                  type="button"
                  className={`${styles.item} ${activeId === a.id ? styles.current : ""} ${expanded.has(a.id) ? styles.expanded : ""}`}
                  onClick={() => void toggleBundle(a)}
                >
                  <span className={styles.name}>{a.name}</span>
                  <span className={styles.sub}>{summarizeAgent(a)}</span>
                  {a.active && <span className={styles.star} title="active agent">●</span>}
                  <span className={styles.chev} aria-hidden>
                    {expanded.has(a.id) ? "▾" : "▸"}
                  </span>
                </button>

                {expanded.has(a.id) && (
                  <div className={styles.bundle}>
                    {bundle === undefined && (
                      <p className={styles.note}>
                        {bundleErrors[a.id] ?? "loading document…"}
                      </p>
                    )}
                    {bundle && b && (
                      <>
                        {onEditOnCanvas && (
                          <div className={styles.actions}>
                            <button
                              type="button"
                              className={styles.action}
                              onClick={() => onEditOnCanvas(a.id)}
                              title="Open this saved document as an agent node on the canvas — edit, Save/Activate, and test the MCP aliases there (the canvas draft starts in sync with the saved document)"
                            >
                              edit on canvas →
                            </button>
                          </div>
                        )}
                        <p className={styles.row}>
                          <span className={styles.tag}>persona</span>
                          <span className={styles.value}>
                            {b.persona ? "prompt set (replaces the default)" : "default AGENTS.md"}
                          </span>
                        </p>
                        {b.model && (
                          <p className={styles.row}>
                            <span className={styles.tag}>model</span>
                            <span className={styles.value}>
                              {b.model.id} <span className={styles.faint}>({b.model.provider})</span>
                            </span>
                          </p>
                        )}
                        {b.mcp.map((m) => (
                          <p key={m.alias} className={styles.row}>
                            <span className={styles.tag}>{m.alias}</span>
                            <span className={styles.value}>
                              {m.transport ?? "?"} — {m.endpoint ?? "no endpoint"}
                            </span>
                          </p>
                        ))}
                        {b.skills.map((s) => (
                          <p key={s.path} className={styles.row}>
                            <span className={styles.tag}>skill</span>
                            <span className={styles.value}>
                              {s.path}
                              {s.tools.length > 0 ? ` (+ ${s.tools.join(", ")})` : ""}
                            </span>
                          </p>
                        ))}
                        {b.settings.map((s) => (
                          <p key={s} className={styles.row}>
                            <span className={styles.tag}>setting</span>
                            <span className={styles.value}>{s}</span>
                          </p>
                        ))}

                        {/* the distinguished child: the one loop that runs */}
                        <div className={styles.loop}>
                          {bundle.loop?.ref ? (
                            <>
                              <button
                                type="button"
                                className={styles.loopToggle}
                                onClick={() => void toggleLoop(a, bundle)}
                              >
                                <span className={styles.tagLoop}>loop</span>
                                <span className={styles.value}>
                                  {bundle.loop.ref} — {loopView ? (loopView.state === "ok" ? `${loopView.nodes.length} nodes` : "…") : "expand"}
                                </span>
                                <span className={styles.chev} aria-hidden>
                                  {loopView ? "▾" : "▸"}
                                </span>
                              </button>
                              {loopView?.state === "ok" && (
                                <div className={styles.loopGraph}>
                                  {loopView.nodes.map((n) => (
                                    <span key={n.id} className={styles.nodeChip}>
                                      <b>{n.id}</b> · {n.primitive}
                                    </span>
                                  ))}
                                  <span className={styles.loopEdgeCount}>
                                    {loopView.edges} edge{loopView.edges === 1 ? "" : "s"}
                                  </span>
                                  <button
                                    type="button"
                                    className={styles.openLoop}
                                    onClick={() => onOpenLoop(bundle.loop?.ref ?? "")}
                                    title="Load this turn-graph into the canvas (a saved document — safe to edit & save under its own id)"
                                  >
                                    open in canvas →
                                  </button>
                                </div>
                              )}
                              {loopView?.state === "fail" && (
                                <p className={styles.error}>{loopView.message}</p>
                              )}
                              {loopView?.state === "loading" && (
                                <p className={styles.note}>loading the referenced graph…</p>
                              )}
                            </>
                          ) : (
                            <p className={styles.row}>
                              <span className={styles.tagLoop}>loop</span>
                              <span className={styles.value}>built-in “{bundle.loop?.alias ?? "unspecified"}”</span>
                            </p>
                          )}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </li>
            );
          })
        )}
      </ul>
    </section>
  );
}
