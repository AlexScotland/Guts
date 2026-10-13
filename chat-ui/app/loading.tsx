import styles from "./loading.module.css";

export default function Loading() {
  return (
    <div className={styles.container}>
      <span className={styles.icon}>🤖</span>
      <div className={styles.dots}>
        <span>.</span>
        <span>.</span>
        <span>.</span>
      </div>
      <p className={styles.text}>Loading…</p>
    </div>
  );
}
