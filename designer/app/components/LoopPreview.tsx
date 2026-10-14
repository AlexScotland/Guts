"use client";

/**
 * The loop preview — the agent's loop (the one child that runs) SHOWN
 * inside the agent card/panel instead of a bare label: a mini render of
 * its node chips + edge count, and "open in canvas" as the bridge to
 * where loops are actually BUILT.
 *
 * This is VISUAL containment, not a contract change: the document stays
 * `loop: {ref}` — a reference that stays shareable (several agents can
 * point at one saved graph), testable, and activatable on its own. The
 * card just stops looking like a hyperlink where it should show content.
 *
 * Data sources (the division of labor):
 *  - alias loop  → a built-in: an honest chip, there is no document
 *    behind it to render;
 *  - ref naming THE CANVAS's own graph → LIVE: shaped by pure function
 *    `describeLoopCanvas` from the local working state — no fetch,
 *    always fresh, re-rendering as you draw the loop (the common
 *    authoring case: "build a loop, make it an agent");
 *  - ref naming ANOTHER saved graph → fetched once (getGraph +
 *    describeLoopGraph, the Agents-library's shaping, shared), cached
 *    per ref id in this module so re-renders don't re-fetch.
 *
 * The fetch + state machine is component-local (the one component
 * context that needs a persistent single-ref view); the pure shaping
 * stays in lib/agents.ts where it is pinned by the fakes-only tests.
 */

import { useEffect, useState } from "react";
import styles from "./LoopPreview.module.css";
import { getGraph } from "../lib/api";
import {
  describeLoopCanvas,
  describeLoopGraph,
  type LoopView,
} from "../lib/agents";
import type { CanvasGraph } from "../lib/graph";
import type { AgentLoopSlot } from "../lib/agentNode";

type Fetched =
  | { state: "loading" }
  | { state: "ok"; view: LoopView }
  | { state: "fail"; message: string };

/** Resolved loop views stay valid across renders (a saved document only
 *  changes through its own Save door, which is also where we re-fetch on
 *  ref change). Failures are NOT cached: a later effect run (ref change
 *  or remount) retries instead of pinning an old offline 404. */
const resolved = new Map<string, LoopView>();

const MAX_CHIPS = 4; // a card is ~240px wide; the rest stays in the canvas

export default function LoopPreview({
  loop,
  canvasGraph,
  canvasGraphId,
  onOpenLoop,
}: {
  /** The draft's loop slot (the discriminated shape keeps ref XOR alias). */
  loop: AgentLoopSlot;
  /** The canvas's OWN working graph (the live source), or null absent. */
  canvasGraph: CanvasGraph | null;
  /** The canvas graph's id (a ref equal to it is the live case). */
  canvasGraphId: string;
  /** Bridge to the building surface: load the referenced graph on the
   *  canvas (the library's "open in canvas" behavior, shared). */
  onOpenLoop?: (graphId: string) => void;
}) {
  const ref = loop.mode === "ref" ? loop.ref.trim() : "";
  const isLive = ref !== "" && canvasGraph !== null && ref === canvasGraphId;

  // Hooks always run; the effect decides (live ⇒ nothing to fetch).
  const [fetched, setFetched] = useState<Fetched | undefined>(() => {
    if (!ref || isLive) return undefined;
    const hit = resolved.get(ref);
    return hit ? { state: "ok", view: hit } : { state: "loading" };
  });

  useEffect(() => {
    if (!ref || isLive) return; // alias or live — the local state is the truth
    const hit = resolved.get(ref);
    if (hit) {
      setFetched({ state: "ok", view: hit });
      return;
    }
    let cancelled = false;
    setFetched({ state: "loading" });
    getGraph(ref)
      .then((res) => {
        const view = describeLoopGraph(res.graph);
        resolved.set(ref, view);
        if (!cancelled) setFetched({ state: "ok", view });
      })
      .catch((err) => {
        if (!cancelled) {
          setFetched({
            state: "fail",
            message: err instanceof Error ? err.message : String(err),
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [ref, isLive]);

  // ── the three presentations ────────────────────────────────────────────

  if (loop.mode === "alias") {
    return (
      <div className={styles.box}>
        <Header live={false} />
        <p className={styles.alias} title="A built-in loop (plan_execute / direct) — no saved document behind it, so nothing to mini-render">
          loop{" "}
          <b className={styles.aliasName}>“{loop.alias}”</b>
          <span className={styles.faint}> — built-in</span>
        </p>
      </div>
    );
  }

  const view = isLive && canvasGraph ? describeLoopCanvas(canvasGraph) : null;

  if (view) {
    // The LIVE case: the loop is right there on the canvas — we are looking
    // at it through the agent's border, updating as it is drawn.
    return (
      <div className={`${styles.box} ${styles.live}`}>
        <Header live />
        {view.nodes.length === 0 ? (
          <p className={styles.note}>
            empty loop — draw its nodes on the canvas (they appear here as you go)
          </p>
        ) : (
          <Chips view={view} />
        )}
        <span className={styles.faint}>
          {view.nodes.length} node{view.nodes.length === 1 ? "" : "s"} ·{" "}
          {view.edges} edge{view.edges === 1 ? "" : "s"} — this canvas
        </span>
      </div>
    );
  }

  // The FETCHED case: the ref names another saved graph.
  return (
    <div className={styles.box}>
      <Header live={false} refName={ref} />
      {!ref ? (
        <p className={styles.fail}>no ref name typed yet</p>
      ) : fetched?.state === "loading" ? (
        <p className={styles.note}>loading the referenced graph…</p>
      ) : fetched?.state === "fail" ? (
        <p className={styles.fail}>{fetched.message}</p>
      ) : fetched?.state === "ok" ? (
        <>
          {fetched.view.nodes.length === 0 ? (
            <p className={styles.note}>that graph has no nodes yet</p>
          ) : (
            <Chips view={fetched.view} />
          )}
          <span className={styles.faint}>
            {fetched.view.nodes.length} node{fetched.view.nodes.length === 1 ? "" : "s"} ·{" "}
            {fetched.view.edges} edge{fetched.view.edges === 1 ? "" : "s"}
          </span>
          {onOpenLoop && (
            <button
              type="button"
              className={styles.open}
              onClick={() => onOpenLoop(ref)}
              title="Load this saved turn-graph onto the canvas — the place where loops are built (its own id, its own Save/Activate/Run)"
            >
              open in canvas →
            </button>
          )}
        </>
      ) : (
        // ref not yet resolved (first render before the effect lands)
        <p className={styles.note}>loading the referenced graph…</p>
      )}
    </div>
  );
}

/** The box header: the slot name + what this preview stands for. */
function Header({ live, refName }: { live: boolean; refName?: string }) {
  return (
    <header className={styles.head}>
      <span className={styles.tag}>loop</span>
      <span className={styles.headline}>the one child that runs</span>
      {live && <span className={styles.liveTag} title="Ref = the canvas's own graph: this is the live working state, saving the agent persists it first (the save-order)">live · this canvas</span>}
      {refName && <span className={styles.refName} title="loop.ref — a saved turn-graph">{refName}</span>}
    </header>
  );
}

/** The mini render: node chips with connectors, capped for the card
 *  width ("+k" names how much more lives in the canvas/store). */
function Chips({ view }: { view: LoopView }) {
  const shown = view.nodes.slice(0, MAX_CHIPS);
  const more = view.nodes.length - shown.length;
  return (
    <div className={styles.nodes} aria-label="Loop nodes (mini view)">
      {shown.map((n) => (
        <span key={n.id} className={styles.node} title={`${n.id} — a ${n.primitive} node`}>
          <b>{n.id}</b>
          <i>{n.primitive}</i>
        </span>
      ))}
      {more > 0 && (
        <span className={styles.more} title={`${more} more node${more === 1 ? "" : "s"} — the full loop is on the canvas / in the saved graph`}>
          +{more}
        </span>
      )}
    </div>
  );
}
