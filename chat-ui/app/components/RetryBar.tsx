"use client";

import styles from "./RetryBar.module.css";

interface RetryBarProps {
  error: string | null;
  canRetry: boolean;
  onRetry: () => void;
  onDismiss: () => void;
}

export default function RetryBar({
  error,
  canRetry,
  onRetry,
  onDismiss,
}: RetryBarProps) {
  if (!error) return null;

  return (
    <div className={styles.bar} role="alert">
      <svg className={styles.icon} viewBox="0 0 24 24" width="16" height="16" fill="none" aria-hidden="true">
        <path
          d="M12 8v5M12 16.2v.1"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
        />
        <path
          d="M10.3 4.2c-.6-1.1-2-1.1-2.6 0L2.9 15.5c-.6 1.1.2 2.5 1.4 2.5h15.4c1.2 0 2-1.4 1.4-2.5L16.3 4.2c-.6-1.1-2-1.1-2.6 0z"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
      </svg>
      <span className={styles.msg}>{error}</span>
      <div className={styles.actions}>
        {canRetry && (
          <button className={styles.retry} onClick={onRetry}>
            Retry last message
          </button>
        )}
        <button className={styles.dismiss} onClick={onDismiss} aria-label="Dismiss">
          <svg viewBox="0 0 24 24" width="14" height="14" stroke="currentColor" strokeWidth="2" fill="none">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>
    </div>
  );
}
