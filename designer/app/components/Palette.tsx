"use client";

import styles from "./Palette.module.css";
import type { PrimitiveMeta } from "../lib/graph";

interface Props {
  primitives: PrimitiveMeta[];
  apiOnline: boolean;
  onAdd: (primitive: string) => void;
}

/** The 8-primitive vocabulary (one door: the API's `primitives` block when
 *  reachable, the local frozen table when the backend is offline). */
export default function Palette({ primitives, apiOnline, onAdd }: Props) {
  return (
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
  );
}
