"use client";

import { useCallback, useEffect, useState } from "react";
import {
  activateComponents,
  getComponents,
  getAgents,
  getAgent,
  activateAgent,
  testAgentMcp,
} from "../lib/api";
import type {
  AgentsStatus,
  AgentDetail,
  ComponentsStatus,
  McpTestResult,
} from "../lib/types";
import styles from "./ComponentPanel.module.css";

const SLOT_ORDER = [
  "goal_resolver",
  "planner",
  "executor",
  "evaluator",
  "memory",
  "loop",
];

type McpTestState =
  | { state: "testing" }
  | ({ state: "ok" } & McpTestResult)
  | { state: "fail"; message: string };

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ComponentPanel({ open, onClose }: Props) {
  const [status, setStatus] = useState<ComponentsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // `${scope}:${name}`
  const [flash, setFlash] = useState<string | null>(null);

  // The agent-graph surface (docs/agent-graph.md): which bundle is active,
  // and its MCP connections (list / enable / test).
  const [agents, setAgents] = useState<AgentsStatus | null>(null);
  const [activeDoc, setActiveDoc] = useState<AgentDetail | null>(null);
  const [mcpTests, setMcpTests] = useState<Record<string, McpTestState>>({});

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [components, agentStatus] = await Promise.all([
        getComponents(),
        getAgents(),
      ]);
      setStatus(components);
      setAgents(agentStatus);
      if (agentStatus.active) {
        try {
          setActiveDoc(await getAgent(agentStatus.active));
        } catch {
          setActiveDoc(null); // the list still worked; the document didn't
        }
        setMcpTests({});
      } else {
        setActiveDoc(null);
        setMcpTests({});
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load components.");
    }
  }, []);

  useEffect(() => {
    if (open) void refresh();
  }, [open, refresh]);

  if (!open) return null;

  const activate = async (scope: string, name: string, payload: { preset?: string; slot?: string; alias?: string }) => {
    const key = `${scope}:${name}`;
    setBusy(key);
    setError(null);
    setFlash(null);
    try {
      const next = await activateComponents(payload);
      setStatus(next);
      setFlash(
        scope === "preset"
          ? `Preset “${name}” active from the next turn.`
          : `Slot ${name} activated from the next turn.`
      );
      window.setTimeout(() => setFlash(null), 4000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Activation failed.");
      await refresh().catch(() => {});
    } finally {
      setBusy(null);
    }
  };

  const applyAgent = async (agentId: string) => {
    setBusy(`agent:${agentId}`);
    setError(null);
    setFlash(null);
    try {
      const next = await activateAgent(agentId);
      setAgents(next);
      if (next.active) {
        try {
          setActiveDoc(await getAgent(next.active));
        } catch {
          setActiveDoc(null);
        }
        setMcpTests({});
      }
      // the bind swapped slot aliases (loop/settings) — mirror it in the
      // components view too:
      setStatus(await getComponents());
      setFlash(`Agent “${agentId}” bound — active from the next turn.`);
      window.setTimeout(() => setFlash(null), 4000);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Activation failed (did the bind reject it?)."
      );
      await refresh().catch(() => {});
    } finally {
      setBusy(null);
    }
  };

  const runMcpTest = async (alias: string) => {
    const activeId = agents?.active;
    if (!activeId) return;
    setMcpTests((m) => ({ ...m, [alias]: { state: "testing" } }));
    try {
      const result = await testAgentMcp(activeId, alias);
      setMcpTests((m) => ({ ...m, [alias]: { state: "ok", ...result } }));
    } catch (err) {
      setMcpTests((m) => ({
        ...m,
        [alias]: { state: "fail", message: err instanceof Error ? err.message : "Test failed." },
      }));
    }
  };

  const slots = status
    ? SLOT_ORDER.filter((s) => status.slots[s]).map((s) => [s, status.slots[s]] as const)
    : [];
  const presets = status ? Object.keys(status.presets) : [];
  const mcpEntries = activeDoc?.agent.mcp ?? [];

  return (
    <section className={styles.panel} aria-label="Agent components">
      <div className={styles.head}>
        <div className={styles.titleBlock}>
          <h2 className={styles.title}>Agent components</h2>
          <p className={styles.hint}>
            Swap loop parts live. Changes apply from the next turn — the current
            one finishes on the set it started with.
          </p>
        </div>
        <div className={styles.headActions}>
          <button className={styles.ghost} onClick={() => void refresh()} disabled={busy !== null}>
            Refresh
          </button>
          <button className={styles.ghost} onClick={onClose} aria-label="Close component panel">
            Close
          </button>
        </div>
      </div>

      {busy && <p className={styles.busy}>Applying {busy.split(":")[1]}…</p>}
      {flash && <p className={styles.flash}>{flash}</p>}
      {error && <p className={styles.error}>{error}</p>}

      {!status && !error && <p className={styles.error}>Loading components…</p>}

      {status && (
        <>
          <div className={styles.agentSection}>
            <h3 className={styles.sectionTitle}>Agent</h3>
            <p className={styles.sectionHint}>
              The capability bundle behind your turns — persona, model, MCP and
              skills, bound into the live context (the loop is unchanged).
            </p>
            {!agents && <p className={styles.busy}>Loading agents…</p>}
            {agents && agents.agents.length === 0 && (
              <p className={styles.sectionHint}>
                No saved agents yet — the built-in persona is in effect.
              </p>
            )}
            {agents &&
              agents.agents.map((a) => (
                <div key={a.id} className={styles.agent}>
                  <div className={styles.agentHead}>
                    <span className={styles.agentName}>{a.name}</span>
                    <span className={styles.agentId}>{a.id}</span>
                    {a.active && <span className={styles.activeTag}>active</span>}
                    {!a.active && (
                      <button
                        className={styles.ghost}
                        disabled={busy !== null}
                        onClick={() => void applyAgent(a.id)}
                        title="Bind this document (model/MCP/skills/persona) and make it active from the next turn"
                      >
                        Activate
                      </button>
                    )}
                  </div>
                  <div className={styles.agentSummary}>
                    {a.loop ? <span>loop: {a.loop}</span> : null}
                    {a.model ? <span> · model: {a.model}</span> : null}
                    {a.mcp.length > 0 ? <span> · mcp: {a.mcp.join(", ")}</span> : null}
                    {a.skills.length > 0 ? <span> · skills: {a.skills.length}</span> : null}
                  </div>
                </div>
              ))}

            {agents?.active && (
              <div className={styles.mcp}>
                <div className={styles.mcpHead}>MCP — agent “{agents.active}”</div>
                {mcpEntries.length === 0 && (
                  <p className={styles.sectionHint}>
                    This agent declares no MCP servers.
                  </p>
                )}
                {mcpEntries.map((e) => {
                  const t = mcpTests[e.alias];
                  return (
                    <div key={e.alias} className={styles.mcpRow}>
                      <span className={styles.mcpAlias}>{e.alias}</span>
                      {e.transport && (
                        <span className={styles.mcpTransport}>{e.transport}</span>
                      )}
                      <span className={styles.mcpEndpoint}>
                        {e.url ?? (e.command ? e.command.join(" ") : "")}
                      </span>
                      <button
                        className={styles.ghost}
                        disabled={busy !== null || t?.state === "testing"}
                        onClick={() => void runMcpTest(e.alias)}
                        title="Open a fresh connection and list the tools the server advertises"
                      >
                        {t?.state === "testing" ? "Testing…" : "Test"}
                      </button>
                      {t && t.state === "ok" && (
                        <span className={styles.mcpResultOk}>
                          {t.detail}
                          {t.tools.length > 0 ? ` — ${t.tools.join(", ")}` : ""}
                        </span>
                      )}
                      {t && t.state === "fail" && (
                        <span className={styles.mcpResultFail}>{t.message}</span>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {presets.length > 0 && (
            <div className={styles.presets}>
              {presets.map((name) => (
                <button
                  key={name}
                  className={`${styles.chip} ${styles[`${name}Chip`] || ""}`}
                  onClick={() => void activate("preset", name, { preset: name })}
                  disabled={busy !== null}
                  title={`All slots: ${Object.entries(status.presets[name] || {})
                    .map(([slot, alias]) => `${slot}=${alias}`)
                    .join(", ")}`}
                >
                  {name}
                </button>
              ))}
            </div>
          )}

          <div className={styles.slots}>
            {slots.map(([name, info]) => {
              const active = info.available.find((a) => a.alias === info.active);
              return (
                <div key={name} className={styles.slot}>
                  <div className={styles.slotHead}>
                    <span className={styles.slotName}>{name}</span>
                    <span className={styles.activeBadge} title={active?.description || ""}>
                      {info.active ?? "—"}
                    </span>
                  </div>
                  {active?.description && (
                    <p className={styles.desc}>{active.description}</p>
                  )}
                  {info.available.length > 1 && (
                    <div className={styles.alternatives}>
                      {info.available.map((option) => (
                        <button
                          key={option.alias}
                          className={`${styles.alt} ${option.alias === info.active ? styles.altActive : ""}`}
                          onClick={() =>
                            void activate("slot", name, { slot: name, alias: option.alias })
                          }
                          disabled={busy !== null || option.alias === info.active}
                          title={`${option.description} (v${option.version}) · applies from the next turn`}
                        >
                          {option.alias}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
