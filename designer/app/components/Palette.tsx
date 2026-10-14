"use client";

import styles from "./Palette.module.css";
import type { PrimitiveMeta } from "../lib/graph";

interface Props {
  primitives: PrimitiveMeta[];
  apiOnline: boolean;
  onAdd: (primitive: string) => void;
  /** Add an AGENT node (docs/agent-graph.md — the layer above). Optional so
   *  the primitive-palette surface stays usable standalone. */
  onAddAgent?: () => void;
}

/** The 8-primitive vocabulary (one door: the API's `primitives` block when
 *  reachable, the local frozen table when the backend is offline) + the
 *  agent node — the SECOND document family (the capability bundle around
 *  exactly one loop child, saved through its own REST door). */
export default function Palette({ primitives, apiOnline, onAdd, onAddAgent }: Props) {
  return (
    <>
      <section className={styles.palette} aria-label="Node primitives">
        <header className={styles.head}>
          <h2 className={styles.title}>Primitives</h2>
          {!apiOnline && (
            <span className={styles.fallback} title="Fetched locally while the backend is offline — the API's primitives block wins when it is up.">
              offline table
            </span>
          )}
        </header>
        <div className={styles.list}>
          {primitives.map((p) => (
            <button
              key={p.primitive}
              type="button"
              className={styles.item}
              onClick={() => onAdd(p.primitive)}
              title={p.description}
            >
              <span className={styles.top}>
                <span className={styles.name}>{p.primitive}</span>
                {p.side_effects && <span className={styles.se}>side effects</span>}
              </span>
              <span className={styles.ports}>{p.provides.join(" · ")}</span>
            </button>
          ))}
        </div>
      </section>

      {onAddAgent && (
        <section className={styles.palette} aria-label="Agent node">
          <header className={styles.head}>
            <h2 className={styles.title}>Agent</h2>
            <span
              className={styles.fallback}
              title="A capability-composition document around exactly one loop child (a saved turn-graph ref or a built-in alias) — the layer above the graph."
            >
              layer above
            </span>
          </header>
          <div className={styles.list}>
            <button
              type="button"
              className={styles.item}
              onClick={onAddAgent}
              title="Add an agent node to the canvas: persona + model + mcp + skills + settings, around one loop (ref XOR alias). Everything saves through POST /api/agents — the backend validator stays the authority."
            >
              <span className={styles.top}>
                <span className={styles.name}>+ add agent</span>
              </span>
              <span className={styles.ports}>persona · model · mcp · skills · loop</span>
            </button>
          </div>
        </section>
      )}
    </>
  );
}
