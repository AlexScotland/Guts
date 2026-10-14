"use client";

/**
 * The agent node's inspector — the authoring surface over the
 * POST /api/agents door (docs/agent-graph.md, "The agent node on the
 * canvas"). Every field edits the DRAFT (the canvas state); nothing is
 * written until Save hits the REST door, where the closed-vocabulary
 * validator remains the authority (a 400 is the honest save failure).
 * The preflight issues list is a cheap mirror of that validator — an
 * early preview, never a new authority.
 */

import { useEffect, useRef, useState } from "react";
import styles from "./AgentPanel.module.css";
import {
  AGENT_LOOP_ALIASES,
  AGENT_PROVIDERS,
  AGENT_SLOT_NAMES,
  checkAgentDraft,
  loopSlotLine,
  newMcpRow,
  newSettingRow,
  newSkillRow,
  type AgentDraft,
  type CanvasAgent,
} from "../lib/agentNode";

interface Props {
  agent: CanvasAgent;
  savedGraphIds: string[];
  graphId: string;
  otherAgentIds: string[];
  busyLabel: string | null;
  onDraft: (id: string, patch: Partial<AgentDraft>) => void;
  /** Returns an error message, or null when committed. */
  onRename: (oldId: string, nextId: string) => string | null;
  onSave: (id: string) => void;
  onActivate: (id: string) => void;
  onDeleteSaved: (id: string) => void;
  onTestMcp: (id: string, alias: string) => void;
}

export default function AgentPanel({
  agent,
  savedGraphIds,
  graphId,
  otherAgentIds,
  busyLabel,
  onDraft,
  onRename,
  onSave,
  onActivate,
  onDeleteSaved,
  onTestMcp,
}: Props) {
  const { draft } = agent;
  const issues = checkAgentDraft(draft, agent.id);
  const busy = busyLabel !== null;
  // A const capture: TS keeps a const's narrowed type INSIDE closures the
  // narrowing branch creates (a `draft.model` inside an onChange arrow would
  // re-widen to `… | null` and lose the `id` in the spread).
  const model = draft.model;

  // ── ID rename: DRAFT + COMMIT (NodePanel's idiom) ────────────────────────
  // The id is the agent's REST IDENTITY (which saved document this node
  // saves to); unlike node properties, it changes identity — so it edits a
  // draft and commits on Enter / unmount, validating on commit.
  const [idDraft, setIdDraft] = useState(agent.id);
  const [idErr, setIdErr] = useState<string | null>(null);

  const commitId = () => {
    const next = idDraft.replace(/\s/g, "");
    if (!next) {
      setIdErr("id can't be empty");
      return;
    }
    const err = onRename(agent.id, next);
    setIdErr(err);
  };

  // Commit a pending rename on real unmount (inspector closed / node
  // switched) so a half-typed id isn't lost — NodePanel's exact pattern.
  const idLatest = useRef({ draft: idDraft, cur: agent.id });
  idLatest.current = { draft: idDraft, cur: agent.id };
  useEffect(() => {
    return () => {
      const { draft: d, cur } = idLatest.current;
      const next = d.replace(/\s/g, "");
      if (next && next !== cur) onRename(cur, next);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Convenience mappers: patch one row in place.
  const patchMcp = (i: number, patch: Partial<AgentDraft["mcp"][number]>) =>
    onDraft(agent.id, { mcp: draft.mcp.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const patchSkill = (i: number, patch: Partial<AgentDraft["skill"][number]>) =>
    onDraft(agent.id, { skill: draft.skill.map((r, j) => (j === i ? { ...r, ...patch } : r)) });
  const patchSetting = (i: number, patch: Partial<AgentDraft["settings"][number]>) =>
    onDraft(agent.id, {
      settings: draft.settings.map((r, j) => (j === i ? { ...r, ...patch } : r)),
    });

  const loopRefValue = draft.loop.mode === "ref" ? draft.loop.ref : "";
  const loopAliasValue =
    draft.loop.mode === "alias"
      ? draft.loop.alias
      : AGENT_LOOP_ALIASES[0];
  const refListId = `agentRef-${agent.id}`;
  const refOptions = [
    graphId,
    ...savedGraphIds.filter((g) => g !== graphId),
    ...(draft.loop.mode === "ref" && !savedGraphIds.includes(draft.loop.ref) && draft.loop.ref
      ? [draft.loop.ref]
      : []),
  ];

  return (
    <div className={styles.panel}>
      {issues.length > 0 && (
        <div className={styles.issues} role="alert">
          {issues.map((i) => (
            <span key={i.message} className={styles.issue}>
              {i.message}
            </span>
          ))}
          <span className={styles.hint}>
            Preflight mirror of the backend validator — Save is blocked until
            these are clear (the backend stays the authority when reachable).
          </span>
        </div>
      )}

      {/* identity + name — the saved document's face */}
      <label className={styles.field}>
        <span>
          id{" "}
          <em>
            (its REST identity — saving writes to this id; renaming starts a
            new agent)
          </em>
        </span>
        <input
          value={idDraft}
          spellCheck={false}
          onChange={(e) => {
            setIdDraft(e.target.value);
            setIdErr(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitId();
          }}
        />
        {idErr ? <span className={styles.err}>{idErr}</span> : null}
      </label>

      <label className={styles.field}>
        <span>name (display; optional)</span>
        <input
          value={draft.name}
          placeholder={agent.id}
          onChange={(e) => onDraft(agent.id, { name: e.target.value })}
        />
      </label>

      <label className={styles.field}>
        <span>prompt (persona — replaces AGENTS.md when set; optional)</span>
        <textarea
          rows={3}
          value={draft.prompt}
          placeholder="You are a research specialist. Cite sources. Stay on the ask."
          onChange={(e) => onDraft(agent.id, { prompt: e.target.value })}
        />
      </label>

      {/* model */}
      {model ? (
        <div>
          <div className={styles.pair}>
            <label className={styles.field}>
              <span>model provider</span>
              <select
                value={model.provider}
                onChange={(e) =>
                  onDraft(agent.id, {
                    model: { ...model, provider: e.target.value },
                  })
                }
              >
                {AGENT_PROVIDERS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <div style={{ flex: 1.6, minWidth: 0, display: "flex", gap: 4 }}>
              <label className={styles.field} style={{ flex: 1 }}>
                <span>model id</span>
                <input
                  value={model.id}
                  spellCheck={false}
                  placeholder="Duggles/qwen-3-8-larger-context:latest"
                  onChange={(e) =>
                    onDraft(agent.id, {
                      model: { ...model, id: e.target.value },
                    })
                  }
                />
              </label>
              <button
                type="button"
                className={styles.miniDanger}
                title="Remove the model override (the harness default applies)"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() => onDraft(agent.id, { model: null })}
              >
                ×
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className={styles.pair}>
          <span className={styles.muted}>model — no override (harness default)</span>
          <button
            type="button"
            className={styles.add}
            style={{ alignSelf: "center" }}
            onClick={() =>
              onDraft(agent.id, { model: { provider: AGENT_PROVIDERS[0], id: "" } })
            }
          >
            + set model
          </button>
        </div>
      )}

      {/* mcp[] — tool sources (a set, not a pipeline) */}
      <div>
        <h3 className={styles.sectionTitle}>mcp (tool sources)</h3>
        <div className={styles.entryList}>
          {draft.mcp.length === 0 && (
            <span className={styles.muted}>none declared</span>
          )}
          {draft.mcp.map((row, i) => (
            <div key={i} className={styles.entry}>
              <input
                className={styles.alias}
                value={row.alias}
                placeholder="alias"
                spellCheck={false}
                onChange={(e) => patchMcp(i, { alias: e.target.value })}
              />
              <select
                className={styles.transport}
                value={row.transport}
                onChange={(e) => patchMcp(i, { transport: e.target.value as never })}
                title="transport: stdio = a local command; sse/http = a URL"
              >
                <option value="">—</option>
                <option value="stdio">stdio</option>
                <option value="sse">sse</option>
                <option value="http">http</option>
              </select>
              {row.transport === "stdio" ? (
                <input
                  className={styles.grow}
                  value={row.command}
                  placeholder="command (argv: python -m my_mcp)"
                  spellCheck={false}
                  onChange={(e) => patchMcp(i, { command: e.target.value })}
                />
              ) : (
                <input
                  className={styles.grow}
                  value={row.url}
                  placeholder={
                    row.transport ? "url" : "url (once a non-stdio transport is set)"
                  }
                  spellCheck={false}
                  onChange={(e) => patchMcp(i, { url: e.target.value })}
                />
              )}
              <button
                type="button"
                className={styles.mini}
                disabled={busy || !row.alias.trim()}
                title="Save this agent, then open a FRESH connection to this alias and list its tools (a genuine check — an unreachable server fails visibly)"
                onClick={() => onTestMcp(agent.id, row.alias.trim())}
              >
                test
              </button>
              <button
                type="button"
                className={styles.miniDanger}
                aria-label={`Remove mcp row ${i + 1}`}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() =>
                  onDraft(agent.id, { mcp: draft.mcp.filter((_, j) => j !== i) })
                }
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            className={styles.add}
            onClick={() => onDraft(agent.id, { mcp: [...draft.mcp, newMcpRow()] })}
          >
            + add mcp
          </button>
        </div>
      </div>

      {/* skill[] — instruction fragments (+ optional bundled tools) */}
      <div>
        <h3 className={styles.sectionTitle}>skills (instruction fragments)</h3>
        <div className={styles.entryList}>
          {draft.skill.length === 0 && (
            <span className={styles.muted}>none declared</span>
          )}
          {draft.skill.map((row, i) => (
            <div key={i} className={styles.entry}>
              <input
                className={styles.grow}
                value={row.path}
                placeholder="path under app/ (e.g. skills/citation.md)"
                spellCheck={false}
                onChange={(e) => patchSkill(i, { path: e.target.value })}
              />
              <input
                className={styles.grow}
                value={row.tools}
                placeholder="bundled tools (comma-separated; optional)"
                spellCheck={false}
                onChange={(e) => patchSkill(i, { tools: e.target.value })}
              />
              <button
                type="button"
                className={styles.miniDanger}
                aria-label={`Remove skill row ${i + 1}`}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() =>
                  onDraft(agent.id, { skill: draft.skill.filter((_, j) => j !== i) })
                }
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            className={styles.add}
            onClick={() =>
              onDraft(agent.id, { skill: [...draft.skill, newSkillRow()] })
            }
          >
            + add skill
          </button>
        </div>
      </div>

      {/* settings — slot-alias overrides (keys ⊆ the six slots) */}
      <div>
        <h3 className={styles.sectionTitle}>settings (slot overrides)</h3>
        <div className={styles.entryList}>
          {draft.settings.length === 0 && (
            <span className={styles.muted}>
              none (e.g. memory: trimmed — the slot's alias)
            </span>
          )}
          {draft.settings.map((row, i) => (
            <div key={i} className={styles.entry}>
              <select
                style={{ width: 130 }}
                value={row.slot}
                onChange={(e) => patchSetting(i, { slot: e.target.value })}
              >
                <option value="">slot…</option>
                {AGENT_SLOT_NAMES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <input
                className={styles.grow}
                value={row.alias}
                placeholder="alias (e.g. trimmed)"
                spellCheck={false}
                onChange={(e) => patchSetting(i, { alias: e.target.value })}
              />
              <button
                type="button"
                className={styles.miniDanger}
                aria-label={`Remove setting row ${i + 1}`}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={() =>
                  onDraft(agent.id, {
                    settings: draft.settings.filter((_, j) => j !== i),
                  })
                }
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            className={styles.add}
            onClick={() =>
              onDraft(agent.id, { settings: [...draft.settings, newSettingRow()] })
            }
          >
            + add setting
          </button>
        </div>
      </div>

      {/* the loop slot — the one child that runs: ref XOR alias */}
      <div className={styles.loopBox}>
        <h3 className={styles.sectionTitle} style={{ margin: 0 }}>
          loop — the one child that runs ({loopSlotLine(draft.loop)})
        </h3>
        <div className={styles.radioRow}>
          <label>
            <input
              type="radio"
              name={`loopMode-${agent.id}`}
              checked={draft.loop.mode === "ref"}
              onChange={() =>
                onDraft(agent.id, {
                  // The click only fires when the radio is UNCHECKED (a
                  // re-click of the active mode is a no-op), so the previous
                  // loop is always an alias here — pick the best available
                  // saved id: this canvas's graph when it is already a saved
                  // document, else the first saved graph.
                  loop: {
                    mode: "ref",
                    ref:
                      graphId && savedGraphIds.includes(graphId)
                        ? graphId
                        : savedGraphIds[0] ?? "",
                  },
                })
              }
            />
            saved turn-graph (ref)
          </label>
          <label>
            <input
              type="radio"
              name={`loopMode-${agent.id}`}
              checked={draft.loop.mode === "alias"}
              onChange={() =>
                onDraft(agent.id, {
                  loop:
                    draft.loop.mode === "alias"
                      ? { mode: "alias", alias: loopAliasValue }
                      : { mode: "alias", alias: AGENT_LOOP_ALIASES[0] },
                })
              }
            />
            built-in alias
          </label>
        </div>
        {draft.loop.mode === "ref" ? (
          <label className={styles.field}>
            <span>ref (a SAVED graph id — save the loop before the agent)</span>
            <input
              list={refListId}
              value={loopRefValue}
              placeholder={graphId || "a saved graph id"}
              spellCheck={false}
              onChange={(e) =>
                onDraft(agent.id, { loop: { mode: "ref", ref: e.target.value } })
              }
            />
            <datalist id={refListId}>
              {refOptions.map((g) => (
                <option key={g} value={g}>
                  {g === graphId ? `${g} (this canvas)` : g}
                </option>
              ))}
            </datalist>
          </label>
        ) : (
          <label className={styles.field}>
            <span>alias (built-in loop — no saved document behind it)</span>
            <select
              value={loopAliasValue}
              onChange={(e) =>
                onDraft(agent.id, {
                  loop: { mode: "alias", alias: e.target.value },
                })
              }
            >
              {AGENT_LOOP_ALIASES.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      {/* actions — the REST door */}
      <div className={styles.actions}>
        <button
          type="button"
          className={`${styles.btn} ${styles.btnPrimary}`}
          disabled={busy || issues.length > 0}
          title={
            issues.length > 0
              ? "Fix the preflight issues first (the backend validator would 400)"
              : "POST /api/agents — validated on the backend before it is written"
          }
          onClick={() => onSave(agent.id)}
        >
          Save
        </button>
        <button
          type="button"
          className={styles.btn}
          disabled={busy || issues.length > 0}
          title="Save (if unsaved), then POST /api/agents/{id}/activate — bind first, a bind failure is a visible 400"
          onClick={() => onActivate(agent.id)}
        >
          Activate
        </button>
        <button
          type="button"
          className={`${styles.btn} ${styles.btnDanger}`}
          disabled={busy}
          title="DELETE /api/agents/{id} — permanent; the canvas node stays as an unsaved draft"
          onClick={() => onDeleteSaved(agent.id)}
        >
          Delete saved
        </button>
        {busyLabel && <span className={styles.busy}>{busyLabel}</span>}
      </div>

      {otherAgentIds.length > 0 && (
        <span className={styles.hint}>
          Other canvas agents: {otherAgentIds.join(", ")} — ids must be unique
          between them.
        </span>
      )}
    </div>
  );
}
