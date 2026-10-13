import Link from "next/link";
import styles from "./not-found.module.css";

export default function NotFound() {
  return (
    <div className={styles.container}>
      <span className={styles.icon}>🤖</span>
      <h1 className={styles.code}>404</h1>
      <p className={styles.message}>Page not found</p>
      <p className={styles.hint}>
        The page you are looking for does not exist.
      </p>
      <Link href="/" className={styles.link}>
        ← Back to chat
      </Link>
    </div>
  );
}
