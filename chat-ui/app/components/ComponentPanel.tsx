"use client";

import { useCallback, useEffect, useState } from "react";
import {
  activateComponents,
  getComponents,
} from "../lib/api";
import type { ComponentsStatus } from "../lib/types";
import styles from "./ComponentPanel.module.css";

const SLOT_ORDER = [
  "goal_resolver",
  "planner",
  "executor",
  "evaluator",
  "memory",
  "loop",
];

interface Props {
  open: boolean;
  onClose: () => void;
}

export default function ComponentPanel({ open, onClose }: Props) {
  const [status, setStatus] = useState<ComponentsStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null); // `${scope}:${name}`
  const [flash, setFlash] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      setStatus(await getComponents());
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

  const slots = status
    ? SLOT_ORDER.filter((s) => status.slots[s]).map((s) => [s, status.slots[s]] as const)
    : [];
  const presets = status ? Object.keys(status.presets) : [];

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
