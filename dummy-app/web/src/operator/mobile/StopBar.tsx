import { ExperimentStopButton, type StopActions } from "../status/ExperimentStopButton.tsx";
import type { stopBar } from "./stop-bar.ts";
import styles from "./StopBar.module.css";

export function StopBar({ bar, ...actions }: StopActions & { bar: NonNullable<ReturnType<typeof stopBar>> }) {
  return <section className={styles.bar} aria-label="Run in progress">
    <div className={styles.text}>
      <strong className={styles.title}>{bar.title}</strong>
      <span className={styles.detail}>{bar.detail}</span>
    </div>
    <ExperimentStopButton {...actions} stop={bar.stop} label={bar.label} compact />
  </section>;
}
