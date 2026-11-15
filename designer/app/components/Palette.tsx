"use client";

import { useMemo } from "react";
import styles from "./Palette.module.css";
import {
  BUDGETED,
  CONTROL_SOURCES,
  TEMPLATES,
  entryEligible,
  fedBy,
  feedsInto,
  whyNotTypable,
  type PrimitiveMeta,
  type Template,
} from "../lib/graph";

interface Props {
  primitives: PrimitiveMeta[];
  apiOnline: boolean;
  onAdd: (primitive: string) => void;
  /** Add an AGENT node (docs/agent-graph.md — the layer above). Optional so
    *  the primitive-palette surface stays usable standalone. */
  onAddAgent?: () => void;
  /** The primitive of the currently selected canvas node (null = none). The
    *  palette then acts as a compatibility light: items a valid wire exists
    *  for are highlighted with a directional chip (accepts {sel} / feeds {sel}),
    *  everything else is dimmed with the one-line port-table reason. */
  selectionPrimitive?: string | null;
  /** Insert one of the one-click template graphs (pure — page state decides). */
  onTemplate?: (tpl: Template) => void;
}

/** The primitive vocabulary (one door: the API's `primitives` block when
 *  reachable, the local frozen table when the backend is offline) + the
 *  agent node (the SECOND document family, the capability bundle around
 *  exactly one loop child, saved through its own REST door).
 *
 *  When a canvas node is selected, the list doubles as a live "what can I
 *  add next" light — computed with the SAME predicate the wire layer uses
 *  (edgeTypable), so the highlight is exactly what a drag would accept. */
export default function Palette({
  primitives,
  apiOnline,
  onAdd,
  onAddAgent,
  selectionPrimitive = null,
  onTemplate,
}: Props) {
  const meta = useMemo(
    () => new Map(primitives.map((p) => [p.primitive, p])),
    [primitives]
  );

  // The context: which primitives form a valid wire with the selection, in
  // each direction. `selFeedsP` ⟺ selection → P (P receives); `pFeedsSel` ⟺
  // P → selection (P sends).
  const context = useMemo(() => {
    if (!selectionPrimitive) return null;
    const feeds = new Set(feedsInto(selectionPrimitive, meta));
    const fed = new Set(fedBy(selectionPrimitive, meta));
    return { sel: selectionPrimitive, feeds, fed };
  }, [selectionPrimitive, meta]);

  return (
    <>
      {onTemplate && (
        <section className={styles.palette} aria-label="Starting templates">
          <header className={styles.head}>
            <h2 className={styles.title}>Start from an example</h2>
            <span
              className={styles.fallback}
              title="One click adds a small, valid graph (nodes + typed wires) to the canvas right of your current nodes. Tweak it, then save."
            >
              one click
            </span>
          </header>
          <div className={styles.list}>
            {TEMPLATES.map((tpl) => (
              <button
                key={tpl.id}
                type="button"
                className={styles.item}
                onClick={() => onTemplate(tpl)}
                title={tpl.description}
              >
                <span className={styles.name}>{tpl.name}</span>
                <span className={styles.ports}>
                  {Object.keys(tpl.doc.nodes).join(" → ")}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      <section className={styles.palette} aria-label="Node primitives">
        <header className={styles.head}>
          <h2 className={styles.title}>Primitives</h2>
          {!apiOnline && (
            <span className={styles.fallback} title="Fetched locally while the backend is offline — the API's primitives block wins when it is up.">
              offline table
            </span>
          )}
        </header>
        {context && (
          <p className={styles.hintline}>
            {primitives.some((p) => context.feeds.has(p.primitive) || context.fed.has(p.primitive))
              ? "Highlighted accept a valid wire from/with “" + context.sel + "” — the dimmed ones spell out why."
              : "No primitive accepts or feeds “" + context.sel + "”… it only takes its own input. Hover a dimmed one for why."}
          </p>
        )}
        <div className={styles.list}>
          {primitives.map((p) => {
            const selFeedsP = context?.feeds.has(p.primitive) ?? false;
            const pFeedsSel = context?.fed.has(p.primitive) ?? false;
            const compatible = selFeedsP || pFeedsSel;
            const reason =
              context && !compatible
                ? [
                    `“${context.sel}” → ${p.primitive}: ${whyNotTypable(context.sel, p.primitive, meta)}`,
                    `${p.primitive} → “${context.sel}”: ${whyNotTypable(p.primitive, context.sel, meta)}`,
                  ].join("\n")
                : null;
            return (
              <button
                key={p.primitive}
                type="button"
                className={`${styles.item} ${compatible ? styles.compatible : ""} ${
                  context && !compatible ? styles.dim : ""
                }`}
                onClick={() => onAdd(p.primitive)}
                title={[p.description, reason].filter(Boolean).join("\n\n")}
              >
                <span className={styles.top}>
                  <span className={styles.name}>{p.primitive}</span>
                  {entryEligible(p.primitive, meta) && (
                    <span className={`${styles.chip} ${styles.chipEntry}`} title="Eligible to be the graph's single entry node — its inputs are satisfiable from the `goal` alone.">
                      ★ entry
                    </span>
                  )}
                  {CONTROL_SOURCES.has(p.primitive) && (
                    <span className={`${styles.chip} ${styles.chipLoop}`} title="A control edge may leave this node: a bounded retry loop back to an earlier node (max_passes sets the ceiling — the data edges stay acyclic).">
                      ↻ loops
                    </span>
                  )}
                  {p.primitive === "route" && (
                    <span className={styles.chip} title="A dispatch node: needs 2+ outgoing data edges (its choice set) and a `decider` in its config.">
                      2+ out
                    </span>
                  )}
                  {BUDGETED.has(p.primitive) && (
                    <span className={`${styles.chip} ${styles.chipBudget}`} title="Carries a token budget (a default applies when you leave it blank).">
                      ⚡
                    </span>
                  )}
                  {p.side_effects && (
                    <span className={styles.chip} title="Mutates the environment (or writes memory) — the loop semantics treat these as the act nodes.">
                      side effects
                    </span>
                  )}
                  {selFeedsP && (
                    <span className={`${styles.chip} ${styles.chipCompat}`} title={`A ${context!.sel} → ${p.primitive} wire is valid: what ${context!.sel} provides lands in ${p.primitive}'s accepted types.`}>
                      ← {context!.sel}
                    </span>
                  )}
                  {pFeedsSel && (
                    <span className={`${styles.chip} ${styles.chipCompat}`} title={`A ${p.primitive} → ${context!.sel} wire is valid: what ${p.primitive} provides lands in ${context!.sel}'s accepted types.`}>
                      → {context!.sel}
                    </span>
                  )}
                </span>
                <span className={styles.ports}>{p.provides.join(" · ")}</span>
              </button>
            );
          })}
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
