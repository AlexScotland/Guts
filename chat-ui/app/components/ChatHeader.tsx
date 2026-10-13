"use client";

import styles from "./ChatHeader.module.css";
import type { Theme } from "../hooks/useTheme";

interface Props {
  online: boolean | null;
  canClear: boolean;
  theme: Theme;
  onToggleTheme: () => void;
  onClear: () => void;
  componentsOpen: boolean;
  onToggleComponents: () => void;
}

export default function ChatHeader({
  online,
  canClear,
  theme,
  onToggleTheme,
  onClear,
  componentsOpen,
  onToggleComponents,
}: Props) {
  const status =
    online === null ? "checking" : online ? "online" : "offline";
  const statusLabel =
    status === "online"
      ? "Backend connected"
      : status === "offline"
        ? "Backend offline"
        : "Checking backend…";

  return (
    <header className={styles.header}>
      <div className={styles.left}>
        <div className={styles.mark} aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
            <path
              d="M12 2.5l1.9 4.9 4.9 1.9-4.9 1.9L12 16.1l-1.9-4.9-4.9-1.9 4.9-1.9L12 2.5z"
              fill="var(--accent)"
            />
            <circle cx="18.5" cy="17.5" r="3" fill="var(--accent)" opacity="0.55" />
          </svg>
        </div>
        <div className={styles.titles}>
          <h1 className={styles.title}>Learning Harness</h1>
          <p className={styles.subtitle}>
            <span
              className={`${styles.dot} ${styles[`dot_${status}`]}`}
              role="status"
              aria-label={statusLabel}
            />
            {statusLabel} · local agent
          </p>
        </div>
      </div>

      <div className={styles.right}>
        {canClear && (
          <button
            className={styles.ghost}
            onClick={onClear}
            title="Start a new conversation (clears the stored thread)"
          >
            New chat
          </button>
        )}
        <button
          className={styles.ghost}
          onClick={onToggleComponents}
          title={componentsOpen ? "Hide agent components" : "Show agent components (live swap)"}
          aria-pressed={componentsOpen}
        >
          {componentsOpen ? "Components ✓" : "Components"}
        </button>
        <button
          className={styles.iconBtn}
          onClick={onToggleTheme}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
        >
          {theme === "dark" ? (
            <svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor">
              <circle cx="12" cy="12" r="4.4" />
              <g stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                <path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5.1 5.1l1.7 1.7M17.2 17.2l1.7 1.7M18.9 5.1l-1.7 1.7M6.8 17.2l-1.7 1.7" />
              </g>
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="17" height="17" fill="currentColor">
              <path d="M20.4 14.2A8.6 8.6 0 0 1 9.8 3.6a.7.7 0 0 0-.9-.9 9.9 9.9 0 1 0 12.4 12.4.7.7 0 0 0-.9-.9z" />
            </svg>
          )}
        </button>
      </div>
    </header>
  );
}
