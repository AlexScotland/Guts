"use client";

import styles from "./global-error.module.css";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="en">
      <body>
        <div className={styles.container}>
          <span className={styles.icon}>🤖</span>
          <h1 className={styles.title}>Something went wrong</h1>
          <p className={styles.message}>
            {error.message || "An unexpected error occurred."}
          </p>
          <button className={styles.button} onClick={() => reset()}>
            Try again
          </button>
        </div>
      </body>
    </html>
  );
}
