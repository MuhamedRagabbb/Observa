import { useId } from "react";
import { Notice } from "../../shared/ui/Notice.tsx";
import { StatusMarker } from "../../shared/ui/StatusMarker.tsx";
import styles from "../Operator.module.css";
import { healthView } from "./health-view.ts";
import { ObservabilityLinks } from "./ObservabilityLinks.tsx";
import { useServiceHealth } from "./service-health.ts";

export function ServiceHealth({ disconnected }: { disconnected: boolean }) {
  const headingId = useId();
  const { snapshot, unreachable } = useServiceHealth();
  const view = healthView({ snapshot, unreachable: disconnected || unreachable });
  return (
    <section className={styles.monitoringSection} aria-labelledby={headingId}>
      <div className={styles.sectionHeading}>
        <h2 id={headingId} className={styles.sectionTitle}>Service health</h2>
        <span className={styles.sectionIndex} aria-hidden="true">03</span>
      </div>
      <p className={styles.sectionDescription}>From controller health checks.</p>
      {view.notice !== null ? <Notice icon="info" live>{view.notice}</Notice> : null}
      <table className={styles.healthTable}>
        <caption className="visually-hidden">Service health</caption>
        <thead>
          <tr>
            <th scope="col">Service</th>
            <th scope="col">Status</th>
            <th scope="col" className={styles.lastCheck}>Last check</th>
          </tr>
        </thead>
        <tbody>
          {view.rows.map(({ service, shape, tone, label, reasons, lastCheck }) => (
            <tr key={service}>
              <th scope="row">{service}</th>
              <td>
                <StatusMarker shape={shape} tone={tone}>{label}</StatusMarker>
                {reasons !== null ? <small className={styles.healthReasons}>{reasons}</small> : null}
              </td>
              <td className={styles.lastCheck}>{lastCheck === "—" ? "—" : <time>{lastCheck}</time>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <ObservabilityLinks />
    </section>
  );
}
