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
import type { BranchNodeSpec, BranchSpec, EdgeSpec } from "../lib/types";
import { type NodeOutputs, truncateLabel } from "../lib/run";
import { branchChain, checkBranch, isControlSource, nextBranchNodeId } from "../lib/graph";

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
        <BranchManager node={node} meta={meta} onBranches={(b) => onBranches(node.id, b)} />
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

// ── branch manager (parallel only) ──────────────────────────────────────
// Drawer edition of branch visualization: a structured view per branch
// (read-only mini-diagram + node/edge rows + live checkBranch lights)
// INSTEAD of the bare JSON box, which stays available as the "raw JSON"
// escape-hatch mode. Deliberate limits (the backend validator stays the
// authority): inner graphs are kept FLAT (no nested `parallel` — express
// it via raw JSON), node ids are stable (rename = delete + re-add), and
// nothing here is interactive-wire: branches run on fresh blackboards, so
// a nested editable canvas would lie about the isolation contract.

function BranchManager({
  node,
  meta,
  onBranches,
}: {
  node: CanvasNode;
  meta: Map<string, PrimitiveMeta>;
  onBranches: (b: BranchSpec[]) => void;
}) {
  const [mode, setMode] = useState<"structured" | "json">("structured");
  const branches = node.branches;
  const setAt = (i: number, b: BranchSpec) =>
    onBranches(branches.map((x, j) => (j === i ? b : x)));

  return (
    <div className={styles.branchbox}>
      <div className={styles.branchHead}>
        <h3>Inline branches</h3>
        <div className={styles.branchMode}>
          <button
            type="button"
            className={mode === "structured" ? styles.modeOn : styles.mode}
            onClick={() => setMode("structured")}
          >
            structure
          </button>
          <button
            type="button"
            className={mode === "json" ? styles.modeOn : styles.mode}
            onClick={() => setMode("json")}
          >
            raw JSON
          </button>
        </div>
      </div>
      <p className={styles.hint}>
        Each branch is a mini-graph on a <em>fresh blackboard</em>: exactly
        one entry, no <code>act</code> inside, results merged in DECLARED
        order. The branch runs in isolation — a nested wire-canvas would
        mislead, so its diagram is read-only.
      </p>

      {mode === "json" ? (
        <RawBranchEditor branches={branches} onBranches={onBranches} />
      ) : (
        <>
          {branches.map((br, i) => (
            <BranchCard
              key={`${br.name}-${i}`}
              branch={br}
              meta={meta}
              onChange={(b) => setAt(i, b)}
              onRemove={() =>
                onBranches(branches.filter((_, j) => j !== i))
              }
            />
          ))}
          <button
            type="button"
            className={styles.addBtn}
            onClick={() =>
              onBranches([
                ...branches,
                { name: freshBranchName(branches), graph: { nodes: {}, edges: [] } },
              ])
            }
          >
            + add branch
          </button>
        </>
      )}
    </div>
  );
}

function freshBranchName(branches: BranchSpec[]): string {
  const used = new Set(branches.map((b) => b.name));
  if (!used.has("branch")) return "branch";
  let k = 2;
  while (used.has(`branch${k}`)) k += 1;
  return `branch${k}`;
}

/** One branch card: name/budget + mini-diagram + node rows + edge rows +
 *  live checkBranch lights (the same rules the backend enforces). */
function BranchCard({
  branch,
  meta,
  onChange,
  onRemove,
}: {
  branch: BranchSpec;
  meta: Map<string, PrimitiveMeta>;
  onChange: (b: BranchSpec) => void;
  onRemove: () => void;
}) {
  const g = branch.graph;
  const ids = Object.keys(g.nodes);
  const issues = checkBranch(branch, meta);
  const controlSources = ids.filter((id) => isControlSource(g.nodes[id].primitive));

  const removeNode = (id: string) => {
    const nodes = { ...g.nodes };
    delete nodes[id];
    onChange({ ...branch, graph: { ...g, nodes } });
  };

  return (
    <div className={styles.branchCard}>
      <div className={styles.branchCardHead}>
        <input
          value={branch.name}
          spellCheck={false}
          aria-label="branch name"
          onChange={(e) => onChange({ ...branch, name: e.target.value })}
        />
        <label className={styles.budget}>
          budget
          <input
            type="number"
            min={0}
            aria-label="branch budget"
            value={branch.budget ?? ""}
            placeholder="∞"
            onChange={(e) =>
              onChange({
                ...branch,
                budget: e.target.value === "" ? null : Number(e.target.value),
              })
            }
          />
        </label>
        <button type="button" className={styles.branchRemove} onClick={onRemove}>
          remove branch
        </button>
      </div>

      {ids.length > 1 && (
        <span className={styles.hint}>{branchChain(branch).join(" → ")}</span>
      )}

      <div className={styles.branchIssues}>
        {issues.length === 0 ? (
          <span className={styles.okLine}>branch checks: all clear</span>
        ) : (
          issues.map((iss, i) => (
            <span key={i} className={styles.errLine}>
              {iss.message}
            </span>
          ))
        )}
      </div>

      <BranchMiniGraph branch={branch} meta={meta} />

      <div className={styles.rowList}>
        <span className={styles.rowListHead}>nodes</span>
        {ids.map((id) => (
          <InnerNodeRow
            key={id}
            id={id}
            spec={g.nodes[id]}
            meta={meta}
            onChange={(patch) =>
              onChange({
                ...branch,
                graph: {
                  ...g,
                  nodes: { ...g.nodes, [id]: { ...g.nodes[id], ...patch } },
                },
              })
            }
            onRemove={() => removeNode(id)}
          />
        ))}
        <AddNodeRow meta={meta} onAdd={(p) => {
          const id = nextBranchNodeId(ids, p);
          onChange({ ...branch, graph: { ...g, nodes: { ...g.nodes, [id]: { primitive: p } } } });
        }} />
      </div>

      <div className={styles.rowList}>
        <span className={styles.rowListHead}>edges</span>
        {g.edges.map((e, i) => (
          <InnerEdgeRow
            key={i}
            edge={e}
            ids={ids}
            controlSources={controlSources}
            onChange={(patch) =>
              onChange({
                ...branch,
                graph: {
                  ...g,
                  edges: g.edges.map((x, j) => (j === i ? { ...x, ...patch } : x)),
                },
              })
            }
            onRemove={() =>
              onChange({
                ...branch,
                graph: { ...g, edges: g.edges.filter((_, j) => j !== i) },
              })
            }
          />
        ))}
        <AddEdgeRow
          ids={ids}
          controlSources={controlSources}
          onAdd={(e) => onChange({ ...branch, graph: { ...g, edges: [...g.edges, e] } })}
        />
      </div>
    </div>
  );
}

/** Read-only mini-diagram of the branch's inner graph: nodes in declared
 *  order (vertical), input ports on the left, provided ports on the right,
 *  data wires solid, control wires dashed. Deliberately NOT interactive —
 *  branches run on isolated blackboards, so an editable mini-canvas would
 *  mislead (the contract: app/components/primitives/parallel.py). */
function BranchMiniGraph({
  branch,
  meta,
}: {
  branch: BranchSpec;
  meta: Map<string, PrimitiveMeta>;
}) {
  const g = branch.graph;
  const ids = Object.keys(g.nodes);
  const H = 44;
  const GAP = 88;
  const PAD = 10;
  const W = 250;
  const HEIGHT = PAD * 2 + Math.max(1, ids.length) * GAP - (GAP - H);
  const y = (i: number) => PAD + i * GAP;
  const X1 = 30; // node box left
  const X2 = 178; // node box right
  if (ids.length === 0)
    return (
      <div className={styles.branchMini}>
        <span className={styles.miniEmpty}>no nodes yet — add one below</span>
      </div>
    );
  return (
    <div className={styles.branchMini}>
      <svg width={W} height={HEIGHT} aria-label={`branch ${branch.name} diagram`}>
        {g.edges.map((e, i) => {
          const fi = ids.indexOf(e.from);
          const ti = ids.indexOf(e.to);
          if (fi < 0 || ti < 0) return null;
          const x1 = X2;
          const y1 = y(fi) + H / 2;
          const x2v = X1;
          const y2 = y(ti) + H / 2;
          const bend = Math.max(28, Math.abs(y2 - y1) * 0.35);
          const d = `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2v - bend} ${y2}, ${x2v} ${y2}`;
          return (
            <path
              key={i}
              d={d}
              className={e.type === "control" ? styles.edgeControl : styles.edgeData}
            />
          );
        })}
        {ids.map((id, i) => {
          const spec = g.nodes[id];
          const m = meta.get(spec.primitive);
          const inPorts = m ? m.accepts.filter((t) => t !== "goal") : [];
          const outPorts = m ? m.provides : [];
          return (
            <g key={id}>
              <rect x={X1} y={y(i)} width={X2 - X1} height={H} rx={8} className={styles.branchNodeBox} />
              <text x={X1 + 8} y={y(i) + 18} className={styles.branchNodePri}>
                {spec.primitive}
              </text>
              <text x={X1 + 8} y={y(i) + 33} className={styles.branchNodeId}>
                {id}
              </text>
              {inPorts.slice(0, 3).map((t, j) => (
                <text
                  key={t + String(j)}
                  x={X1 - 6}
                  y={y(i) + H / 2 + (j - (Math.min(3, inPorts.length) - 1) / 2) * 10 + 3}
                  textAnchor="end"
                  className={styles.branchPort}
                >
                  {t}
                </text>
              ))}
              {outPorts.slice(0, 3).map((t, j) => (
                <text
                  key={t + String(j)}
                  x={X2 + 6}
                  y={y(i) + H / 2 + (j - (Math.min(3, outPorts.length) - 1) / 2) * 10 + 3}
                  className={styles.branchPort}
                >
                  {t}
                </text>
              ))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Structured inner-node row: primitive (nested `parallel` is excluded —
 *  flat branches in the structured editor; raw JSON is the full-power
 *  path), budget, config JSON. */
function InnerNodeRow({
  id,
  spec,
  meta,
  onChange,
  onRemove,
}: {
  id: string;
  spec: BranchNodeSpec;
  meta: Map<string, PrimitiveMeta>;
  onChange: (patch: Partial<BranchNodeSpec>) => void;
  onRemove: () => void;
}) {
  const [cfgText, setCfgText] = useState(() =>
    JSON.stringify(spec.config ?? {})
  );
  const [cfgBad, setCfgBad] = useState(false);
  const setConfig = (text: string) => {
    setCfgText(text);
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        setCfgBad(false);
        onChange(
          Object.keys(parsed).length > 0 ? { config: parsed } : { config: {} }
        );
      } else {
        setCfgBad(true);
      }
    } catch {
      setCfgBad(true);
    }
  };
  return (
    <div className={styles.nodeRow}>
      <div className={styles.nodeRowHead}>
        <span className={styles.nid}>{id}</span>
        <select
          value={spec.primitive}
          aria-label={`node ${id} primitive`}
          onChange={(e) => onChange({ primitive: e.target.value })}
        >
          {[...meta.keys()]
            .filter((p) => p !== "parallel")
            .map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
        </select>
        <label className={styles.budget}>
          budget
          <input
            type="number"
            min={0}
            aria-label={`node ${id} budget`}
            value={spec.budget ?? ""}
            placeholder="∞"
            onChange={(e) =>
              onChange({ budget: e.target.value === "" ? null : Number(e.target.value) })
            }
          />
        </label>
        <span className={styles.spacer} />
        <button type="button" className={styles.xBtn} title={`remove node ${id}`} onClick={onRemove}>
          ✕
        </button>
      </div>
      <div className={styles.cfgRow}>
        <textarea
          rows={2}
          spellCheck={false}
          aria-label={`node ${id} config JSON`}
          className={cfgBad ? styles.cfgBad : undefined}
          value={cfgText}
          onChange={(e) => setConfig(e.target.value)}
        />
        {cfgBad && <span className={styles.err}>valid JSON object required</span>}
      </div>
    </div>
  );
}

/** Structured inner-edge row: from / to / type (+ max_passes for control).
 *  When type is control, the FROM options are pre-filtered to verdict
 *  nodes (critic/gate) since those are the only legal sources. */
function InnerEdgeRow({
  edge,
  ids,
  controlSources,
  onChange,
  onRemove,
}: {
  edge: EdgeSpec;
  ids: string[];
  controlSources: string[];
  onChange: (patch: Partial<EdgeSpec>) => void;
  onRemove: () => void;
}) {
  const fromOptions =
    edge.type === "control" && controlSources.length > 0 ? controlSources : ids;
  const from =
    edge.from && fromOptions.includes(edge.from) ? edge.from : fromOptions[0];
  return (
    <div className={styles.edgeRow}>
      <select
        value={from}
        aria-label="edge from"
        onChange={(e) => onChange({ from: e.target.value })}
      >
        {fromOptions.map((id) => (
          <option key={id} value={id}>
            {id}
          </option>
        ))}
      </select>
      <span className={styles.arrow}>→</span>
      <select value={edge.to} aria-label="edge to" onChange={(e) => onChange({ to: e.target.value })}>
        {ids.map((id) => (
          <option key={id} value={id}>
            {id}
          </option>
        ))}
      </select>
      <select
        value={edge.type}
        aria-label="edge type"
        onChange={(e) =>
          onChange({
            type: e.target.value as "data" | "control",
            ...(e.target.value === "control" && edge.max_passes == null
              ? { max_passes: 2 }
              : null),
          })
        }
      >
        <option value="data">data</option>
        <option value="control">control</option>
      </select>
      {edge.type === "control" && (
        <label className={styles.budget}>
          max
          <input
            type="number"
            min={1}
            aria-label="edge max_passes"
            value={edge.max_passes ?? ""}
            onChange={(e) =>
              onChange({ max_passes: e.target.value === "" ? undefined : Number(e.target.value) })
            }
          />
        </label>
      )}
      <span className={styles.spacer} />
      <button type="button" className={styles.xBtn} title="remove edge" onClick={onRemove}>
        ✕
      </button>
    </div>
  );
}

function AddNodeRow({
  meta,
  onAdd,
}: {
  meta: Map<string, PrimitiveMeta>;
  onAdd: (primitive: string) => void;
}) {
  const choices = [...meta.keys()].filter((p) => p !== "parallel");
  const [pick, setPick] = useState(choices[0] ?? "");
  return (
    <div className={styles.addRow}>
      <select value={pick} aria-label="new node primitive" onChange={(e) => setPick(e.target.value)}>
        {choices.map((p) => (
          <option key={p} value={p}>
            {p}
          </option>
        ))}
      </select>
      <button
        type="button"
        className={styles.addBtn}
        disabled={!pick}
        onClick={() => pick && onAdd(pick)}
      >
        + add node
      </button>
      <span className={styles.hint}>
        nested <code>parallel</code> goes in raw JSON
      </span>
    </div>
  );
}

function AddEdgeRow({
  ids,
  controlSources,
  onAdd,
}: {
  ids: string[];
  controlSources: string[];
  onAdd: (e: EdgeSpec) => void;
}) {
  const [type, setType] = useState<"data" | "control">("data");
  const [from, setFrom] = useState(ids[0] ?? "");
  const [to, setTo] = useState(ids[1] ?? "");
  if (ids.length < 2)
    return (
      <div className={styles.addRow}>
        <button type="button" className={styles.addBtn} disabled>
          + add edge (needs two nodes)
        </button>
      </div>
    );
  const srcOptions = type === "control" && controlSources.length > 0 ? controlSources : ids;
  const src = srcOptions.includes(from) ? from : srcOptions[0];
  const dst = ids.includes(to) ? to : ids.find((id) => id !== src) ?? ids[0];
  return (
    <div className={styles.addRow}>
      <select
        value={src}
        aria-label="new edge from"
        onChange={(e) => setFrom(e.target.value)}
      >
        {srcOptions.map((id) => (
          <option key={id} value={id}>
            {id}
          </option>
        ))}
      </select>
      <span className={styles.arrow}>→</span>
      <select value={dst} aria-label="new edge to" onChange={(e) => setTo(e.target.value)}>
        {ids.map((id) => (
          <option key={id} value={id}>
            {id}
          </option>
        ))}
      </select>
      <select
        aria-label="new edge type"
        value={type}
        onChange={(e) => setType(e.target.value as "data" | "control")}
      >
        <option value="data">data</option>
        <option value="control">control</option>
      </select>
      <button
        type="button"
        className={styles.addBtn}
        disabled={src === dst}
        onClick={() => {
          const edge: EdgeSpec = { from: src, to: dst, type };
          if (type === "control") edge.max_passes = 2;
          onAdd(edge);
        }}
      >
        + add edge
      </button>
    </div>
  );
}

/** The original raw-JSON branch editor, kept as the escape-hatch mode of
 *  the branch manager (nested `parallel`, any shape the validator
 *  accepts — the backend remains the authority). */
function RawBranchEditor({
  branches,
  onBranches,
}: {
  branches: BranchSpec[];
  onBranches: (b: BranchSpec[]) => void;
}) {
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
    <>
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
    </>
  );
}
