"use client";

import styles from "./Header.module.css";
import type { Theme } from "../hooks/useTheme";

interface Props {
  online: boolean | null;
  activeLoop: string | null;
  activeGraph: string | null;
  theme: Theme;
  onToggleTheme: () => void;
}

export default function Header({
  online,
  activeLoop,
  activeGraph,
  theme,
  onToggleTheme,
}: Props) {
  const status = online === null ? "checking" : online ? "online" : "offline";
  return (
    <header className={styles.header}>
      <div className={styles.left}>
        <div className={styles.mark} aria-hidden="true">
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none">
            <rect x="2.5" y="4" width="6" height="5" rx="1.4" fill="var(--accent)" />
            <rect x="15.5" y="3" width="6" height="5" rx="1.4" fill="var(--accent)" opacity="0.55" />
            <rect x="15.5" y="16" width="6" height="5" rx="1.4" fill="var(--accent)" opacity="0.55" />
            <path
              d="M8.5 6.5h4.2v11H8.5M12.7 12H15.5"
              stroke="var(--text-muted)"
              strokeWidth="1.6"
              fill="none"
            />
          </svg>
        </div>
        <div className={styles.titles}>
          <h1 className={styles.title}>Loop Designer</h1>
          <p className={styles.subtitle}>
            <span
              className={`${styles.dot} ${styles[`dot_${status}`]}`}
              role="status"
              aria-label={status}
            />
            {status === "online"
              ? "backend connected"
              : status === "offline"
                ? "backend offline"
                : "checking backend…"}
            {activeLoop && (
              <>
                {" · "}
                loop <code>{activeLoop}</code>
                {activeGraph && (
                  <>
                    {" · "}
                    graph <code>{activeGraph}</code>
                  </>
                )}
              </>
            )}
          </p>
        </div>
      </div>
      <div className={styles.right}>
        <button
          type="button"
          className={styles.iconBtn}
          onClick={onToggleTheme}
          aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          title="Toggle theme"
        >
          {theme === "dark" ? "☾" : "☀"}
        </button>
      </div>
    </header>
  );
}
