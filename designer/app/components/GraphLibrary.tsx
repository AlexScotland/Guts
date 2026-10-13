"use client";

import styles from "./GraphLibrary.module.css";
import type { GraphMeta } from "../lib/types";

interface Props {
  graphs: GraphMeta[] | null;
  active: string | null;
  currentId: string;
  online: boolean;
  onLoad: (id: string) => void;
  onNew: () => void;
}

/** The saved documents: the JSON files behind GET /api/graphs. */
export default function GraphLibrary({
  graphs,
  active,
  currentId,
  online,
  onLoad,
  onNew,
}: Props) {
  return (
    <section className={styles.library} aria-label="Saved graphs">
      <header className={styles.head}>
        <h2 className={styles.title}>Graphs</h2>
        <button
          type="button"
          className={styles.new}
          onClick={onNew}
          title="Start a fresh graph (a new id — the store keeps history by id)"
        >
          + new
        </button>
      </header>
      <ul className={styles.list}>
        {graphs === null ? (
          !online && (
            <li className={styles.offlineNote}>backend offline — nothing loaded</li>
          )
        ) : graphs.length === 0 ? (
          <li className={styles.emptyNote}>
            No saved graphs yet — compose one and Save.
          </li>
        ) : (
          graphs.map((g) => (
            <li key={g.id}>
              <button
                type="button"
                className={`${styles.item} ${g.id === currentId ? styles.current : ""}`}
                onClick={() => onLoad(g.id)}
              >
                <span className={styles.name}>{g.name}</span>
                <span className={styles.sub}>
                  {g.id} · {g.node_count} nodes
                  {g.active ? " · active" : ""}
                </span>
                {g.active && <span className={styles.star} title="active workflow">●</span>}
              </button>
            </li>
          ))
        )}
      </ul>
    </section>
  );
}
