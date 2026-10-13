"use client";

/**
 * The shared NODE-details panel: the editable config (id / budget /
 * on_failure / config JSON / inline branches) + the richer per-type
 * last-output read. Rendered INSIDE the on-node popover (NodeCard) so the
 * node's own surface owns its inspection — the fixed right bar keeps only
 * graph-level settings, checks and edge config.
 */

import { useEffect, useRef, useState } from "react";
import styles from "./NodePanel.module.css";
import type {
  CanvasGraph,
  CanvasNode,
  PrimitiveMeta,
} from "../lib/graph";
import type { BranchSpec } from "../lib/types";
import { type NodeOutputs, truncateLabel } from "../lib/run";

interface Props {
  node: CanvasNode;
  graph: CanvasGraph;
  meta: Map<string, PrimitiveMeta>;
  onNode: (id: string, patch: Partial<CanvasNode>) => void;
  onBranches: (id: string, branches: BranchSpec[]) => void;
  /** The latest-run `result` payload for this node (the read surface). */
  lastOutputs: NodeOutputs | null;
  /** The record's top-level answer (for the "graph answer" tag). */
  runAnswer: string | null;
}

export default function NodePanel({
  node,
  graph,
  meta,
  onNode,
  onBranches,
  lastOutputs,
  runAnswer,
}: Props) {
  const p = meta.get(node.primitive);
  const incoming = graph.edges.filter((e) => e.to === node.id).length;
  const outgoing = graph.edges.filter((e) => e.from === node.id).length;

  const [configText, setConfigText] = useState(() =>
    JSON.stringify(node.config ?? {}, null, 2)
  );
  const [configErr, setConfigErr] = useState<string | null>(null);

  // ── ID rename: DRAFT + COMMIT, not live per keystroke ────────────────────
  // The other fields patch node PROPERTIES; the ID patch changes the node's
  // IDENTITY, and the canvas keys cards by id (<NodeCard key={node.id}>) —
  // so a live per-keystroke rename REMOUNTS the whole card + this panel on
  // every character, destroying the focused input mid-type (the "type one
  // char -> it deselects" bug) and letting a half-typed id transiently
  // collide with a sibling's. Instead: edit a draft, commit on Enter or
  // when the panel unmounts (close / node switch), validate on commit.
  const [idDraft, setIdDraft] = useState(node.id);
  const [idErr, setIdErr] = useState<string | null>(null);

  const commitId = () => {
    const next = idDraft.replace(/\s/g, "");
    if (!next) {
      setIdErr("id can't be empty");
      return;
    }
    if (next === node.id) {
      setIdDraft(next); // normalize any whitespace
      setIdErr(null);
      return;
    }
    if (graph.nodes.some((n) => n.id === next)) {
      setIdErr(`a node named "${next}" already exists`);
      return;
    }
    setIdErr(null);
    onNode(node.id, { id: next });
  };

  // Commit a pending rename when the panel truly unmounts (inspector closed,
  // selected another node) so a half-typed name isn't lost. The `[]` effect
  // runs its cleanup ONLY on real unmount — never per keystroke — while the
  // ref carries the latest values at that moment.
  const idLatest = useRef({ draft: idDraft, cur: node.id, ids: graph.nodes });
  idLatest.current = { draft: idDraft, cur: node.id, ids: graph.nodes };
  useEffect(() => {
    return () => {
      const { draft, cur, ids } = idLatest.current;
      const next = draft.replace(/\s/g, "");
      if (next && next !== cur && !ids.some((n) => n.id === next)) {
        onNode(cur, { id: next });
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const applyConfig = () => {
    try {
      const parsed = JSON.parse(configText) as unknown;
      if (
        typeof parsed !== "object" ||
        parsed === null ||
        Array.isArray(parsed)
      )
        throw new Error("config must be a JSON object");
      setConfigErr(null);
      onNode(node.id, { config: parsed as Record<string, unknown> });
    } catch (err) {
      setConfigErr(err instanceof Error ? err.message : String(err));
    }
  };

  const took =
    node.primitive === "route" && lastOutputs
      ? typeof lastOutputs.outputs.choice === "string"
        ? lastOutputs.outputs.choice
        : null
      : null;

  return (
    <div className={styles.panel}>
      <div className={styles.meta}>
        <span className={styles.pill}>{node.primitive}</span>
        {node.budget != null && (
          <span className={styles.pill}>budget {node.budget}</span>
        )}
        {node.on_failure && (
          <span className={styles.pill}>{node.on_failure}</span>
        )}
        <span className={styles.pill}>
          {incoming} in · {outgoing} out
        </span>
      </div>

      {lastOutputs && (
        <OutputPanel outputs={lastOutputs} runAnswer={runAnswer} took={took} />
      )}

      <label className={styles.field}>
        <span>ID</span>
        <input
          value={idDraft}
          onChange={(e) => {
            setIdDraft(e.target.value);
            setIdErr(null);
          }}
          // Commit on Enter (or on unmount — see the effect above). Deliberate
          // NO onBlur commit: clicking another field would otherwise commit
          // → rename → key remount mid-click, swallowing the intended click.
          onKeyDown={(e) => {
            if (e.key === "Enter") commitId();
          }}
        />
        {idErr ? <span className={styles.err}>{idErr}</span> : null}
      </label>

      <div className={styles.pair}>
        <label className={styles.field}>
          <span>Budget</span>
          <input
            type="number"
            min={0}
            placeholder="∞"
            value={node.budget ?? ""}
            onChange={(e) =>
              onNode(node.id, {
                budget: e.target.value === "" ? null : Number(e.target.value),
              })
            }
          />
        </label>
        <label className={styles.field}>
          <span>on_failure</span>
          <select
            value={node.on_failure ?? "abort"}
            onChange={(e) => onNode(node.id, { on_failure: e.target.value })}
          >
            <option value="">abort (default)</option>
            <option value="skip">skip</option>
            <option value="retry(1)">retry(1)</option>
            <option value="retry(2)">retry(2)</option>
            <option value="retry(3)">retry(3)</option>
            <option value="retry(5)">retry(5)</option>
          </select>
        </label>
      </div>

      {node.primitive === "route" ? (
        <RouteConfig node={node} graph={graph} onNode={onNode} />
      ) : (
        <>
          <label className={styles.field}>
            <span>
              config{" "}
              {p ? <em>({p.requires.join(", ") || "no inputs"})</em> : null}
            </span>
            <textarea
              rows={4}
              spellCheck={false}
              className={styles.codearea}
              value={configText}
              onChange={(e) => setConfigText(e.target.value)}
            />
            {configErr ? <span className={styles.err}>{configErr}</span> : null}
          </label>
          <button type="button" className={styles.apply} onClick={applyConfig}>
            Apply config
          </button>
        </>
      )}

      {node.primitive === "parallel" && (
        <BranchEditor node={node} onBranches={(b) => onBranches(node.id, b)} />
      )}
    </div>
  );
}

// ── route node config (v1, docs/ai-routing-nodes.md) ──────────────────────

/** The choice set IS the wiring: one description input per outgoing data
 *  edge. Every patch rebuilds `config.options` over the CURRENT edge set so
 *  the config can never drift from the wiring (the validator requires the
 *  keys to equal the choice set exactly); the server validator remains the
 *  authority. The raw config JSON surface is not shown for route — the
 *  structured parts below are the entire config. */
function RouteConfig({
  node,
  graph,
  onNode,
}: {
  node: CanvasNode;
  graph: CanvasGraph;
  onNode: (id: string, patch: Partial<CanvasNode>) => void;
}) {
  const cfg = (node.config ?? {}) as Record<string, unknown>;
  const outs = graph.edges
    .filter((e) => e.from === node.id && e.type === "data")
    .map((e) => e.to);
  const prevOptions = (cfg.options ?? {}) as Record<string, unknown>;
  const options = outs.reduce((acc, target) => {
    acc[target] = typeof prevOptions[target] === "string" ? prevOptions[target] : "";
    return acc;
  }, {} as Record<string, string>);

  const decider = cfg.decider === "llm" ? "llm" : "manual";
  const fallback = typeof cfg.fallback === "string" ? cfg.fallback : "";
  const pinned = typeof cfg.pinned === "string" ? cfg.pinned : "";

  const patch = (next: Record<string, unknown>) =>
    onNode(node.id, {
      config: { ...cfg, ...next, options: (next.options as Record<string, string>) ?? options },
    });

  return (
    <div className={styles.routebox}>
      <h3 className={styles.outTitle}>
        routing{" "}
        <em className={styles.hint}>the outgoing data edges are the choice set</em>
      </h3>

      {outs.length < 2 && (
        <p className={styles.hint}>
          Wire at least two outgoing data edges — they form the choice set.
          A single target is a plain edge; one is rejected by the validator.
        </p>
      )}

      <div className={styles.pair}>
        <label className={styles.field}>
          <span>decider</span>
          <select
            value={decider}
            onChange={(e) => {
              const d = e.target.value;
              if (d === "llm") {
                const next: Record<string, unknown> = { ...cfg, decider: d };
                delete next.pinned; // only valid with the manual decider
                onNode(node.id, { config: { ...next, options } });
              } else {
                const keepPinned =
                  typeof cfg.pinned === "string" && outs.includes(cfg.pinned);
                patch({
                  decider: "manual",
                  pinned: keepPinned ? cfg.pinned : outs[0] ?? "",
                });
              }
            }}
          >
            <option value="manual">manual (pinned, no LLM calls)</option>
            <option value="llm">llm (structured single choice)</option>
          </select>
        </label>

        <label className={styles.field}>
          <span>fallback</span>
          <select
            value={fallback}
            onChange={(e) => {
              const v = e.target.value;
              const next: Record<string, unknown> = { ...cfg };
              if (v) next.fallback = v;
              else delete next.fallback; // "none" = no fallback key at all
              onNode(node.id, { config: { ...next, options } });
            }}
          >
            <option value="">none (invalid choice fails visibly)</option>
            {outs.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>

      {decider === "manual" && (
        <label className={styles.field}>
          <span>pinned (manual decider)</span>
          <select
            value={outs.includes(pinned) ? pinned : outs[0] ?? ""}
            onChange={(e) => patch({ pinned: e.target.value })}
          >
            {outs.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      )}

      {outs.length > 0 && (
        <div className={styles.optList}>
          {outs.map((target) => (
            <label key={target} className={styles.field}>
              <span>
                option → <code>{target}</code>
              </span>
              <input
                value={options[target]}
                placeholder="branch description (what this branch does)"
                onChange={(e) =>
                  patch({
                    options: { ...options, [target]: e.target.value },
                  })
                }
              />
            </label>
          ))}
          <p className={styles.hint}>
            {"The decider sees this table; the canvas shows it on each " +
              "choice-edge. Descriptions must be non-empty when the graph saves."}
          </p>
        </div>
      )}
    </div>
  );
}

// ── last output (the ai-per-node-outputs seam, the read surface) ─────────

function OutputPanel({
  outputs,
  runAnswer,
  took,
}: {
  outputs: NodeOutputs;
  runAnswer: string | null;
  took?: string | null;
}) {
  const entries = Object.entries(outputs.outputs);
  if (entries.length === 0) return null;
  return (
    <div className={styles.outputsBox}>
      <h3 className={styles.outTitle}>
        Last output
        {outputs.branch && <span className={styles.pill}>{outputs.branch}</span>}
      </h3>
      {took && (
        <div className={styles.tookBanner} title="The edge to this node was taken; the other choice-set edges were pruned">
          took branch → <span className={styles.pill}>{took}</span>
        </div>
      )}
      {entries.map(([key, value]) => (
        <div key={key} className={styles.outEntry}>
          <span className={styles.outKey}>
            <span className={styles.pill}>{key}</span>
            {outputs.truncated?.[key] != null && (
              <span
                className={styles.trunc}
                title={`original size: ${truncateLabel(
                  outputs.truncated[key]!
                )}`}
              >
                truncated ({truncateLabel(outputs.truncated[key]!)})
              </span>
            )}
            {runAnswer != null && value === runAnswer && (
              <span
                className={styles.ansTag}
                title="identical to the record's top-level answer"
              >
                graph answer
              </span>
            )}
          </span>
          <OutputValue value={value} />
        </div>
      ))}
    </div>
  );
}

/** Per-type readability (design doc): text as text, plans/lists as lists,
 *  verdicts as pass/fail + fields, everything else as JSON. */
function OutputValue({ value }: { value: unknown }) {
  if (typeof value === "string")
    return <pre className={styles.outText}>{value}</pre>;
  if (Array.isArray(value))
    return (
      <ol className={styles.outList}>
        {value.map((v, i) => (
          <li key={i}>{typeof v === "string" ? v : JSON.stringify(v)}</li>
        ))}
      </ol>
    );
  if (value !== null && typeof value === "object") {
    const o = value as Record<string, unknown>;
    const pass = typeof o.pass === "boolean" ? o.pass : null;
    const fields = Object.entries(o).filter(([k]) => k !== "pass");
    return (
      <div className={styles.outObj}>
        {pass != null && (
          <span className={pass ? styles.pillPass : styles.pillFail}>
            {pass ? "pass" : "fail"}
          </span>
        )}
        {fields.map(([k, v]) => (
          <div key={k} className={styles.outField}>
            <span className={styles.outFieldName}>{k}</span>
            <OutputValue value={v} />
          </div>
        ))}
      </div>
    );
  }
  return <pre className={styles.outText}>{String(value)}</pre>;
}

// ── branch editor (parallel only) ─────────────────────────────────────────

function BranchEditor({
  node,
  onBranches,
}: {
  node: CanvasNode;
  onBranches: (b: BranchSpec[]) => void;
}) {
  const branches = node.branches;
  const [text, setText] = useState(() => JSON.stringify(branches, null, 2));
  const [err, setErr] = useState<string | null>(null);

  const apply = () => {
    try {
      const parsed = JSON.parse(text);
      if (!Array.isArray(parsed) || parsed.length === 0)
        throw new Error("branches must be a non-empty array");
      for (const b of parsed) {
        if (!b?.name || !b?.graph?.nodes)
          throw new Error("each branch needs { name, graph: { nodes } }");
      }
      setErr(null);
      onBranches(parsed as BranchSpec[]);
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    }
  };

  return (
    <div className={styles.branchbox}>
      <h3>Inline branches (JSON)</h3>
      <p className={styles.hint}>
        Each branch is a mini-graph run on a fresh blackboard;{" "}
        <code>act</code> is rejected inside branches.
      </p>
      <textarea
        rows={7}
        spellCheck={false}
        className={styles.codearea}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      {err && <span className={styles.err}>{err}</span>}
      <button type="button" className={styles.apply} onClick={apply}>
        Apply branches
      </button>
    </div>
  );
}
