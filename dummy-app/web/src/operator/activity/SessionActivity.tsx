import { useId } from "react";
import { Notice } from "../../shared/ui/Notice.tsx";
import styles from "../Operator.module.css";
import { useSessionActivity } from "./use-session-activity.ts";

export function SessionActivity({ sessionStart }: { sessionStart: number | null }) {
  const headingId = useId();
  const { items, unreachable } = useSessionActivity(sessionStart);
  return (
    <section className={styles.monitoringSection} aria-labelledby={headingId}>
      <div className={styles.sectionHeading}>
        <h2 id={headingId} className={styles.sectionTitle}>Session activity</h2>
        <span className={styles.sectionIndex} aria-hidden="true">04</span>
      </div>
      {unreachable ? (
        <Notice icon="info" live>Can’t read the controller’s activity log. The list below may be out of date.</Notice>
      ) : null}
      {sessionStart === null || items.length === 0 ? (
        <p className={styles.activityEmpty}>No activity in this session yet.</p>
      ) : (
        <ol className={styles.activityLog} aria-label="Session activity log">
          {items.map(({ key, time, text }) => (
            <li key={key}>
              <time>{time}</time>
              <span>{text}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
