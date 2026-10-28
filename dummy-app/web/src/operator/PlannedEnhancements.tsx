import { useId } from "react";
import styles from "./Operator.module.css";

export function PlannedEnhancements() {
  const headingId = useId();
  return (
    <section className={styles.plannedEnhancements} aria-labelledby={headingId}>
      <div className={styles.sectionHeading}>
        <h2 id={headingId} className={styles.plannedTitle}>Planned enhancements</h2>
        <span className={styles.plannedBadge}>Not available yet</span>
      </div>
      <p className={styles.sectionDescription}>Listed for reference only. None of these can be used from this dashboard.</p>
      <ul className={styles.plannedList}>
        <li>Severity presets with editable parameters</li>
        <li>Affected-order rate for F3 Bad Deployment</li>
        <li>Per-scenario parameters inside the automatic cycle</li>
        <li>Traffic controls with safe operating limits</li>
      </ul>
    </section>
  );
}
