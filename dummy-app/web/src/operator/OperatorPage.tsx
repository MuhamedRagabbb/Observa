// The Operator Dashboard reads controller status, service health and session activity.
// Fault controls follow in later slices; every request here is a GET.
import { useEffect, useState } from "react";
import { CrossAppLink } from "../shared/CrossAppLink.tsx";
import { Shell } from "../shared/Shell.tsx";
import { StatusMarker } from "../shared/ui/StatusMarker.tsx";
import { SessionActivity } from "./activity/SessionActivity.tsx";
import { ServiceHealth } from "./health/ServiceHealth.tsx";
import styles from "./Operator.module.css";
import { PlannedEnhancements } from "./PlannedEnhancements.tsx";
import { useControllerStatus } from "./status/controller-status.ts";
import { ExperimentBanner } from "./status/ExperimentBanner.tsx";
import { experimentView, type Connection } from "./status/experiment.ts";

/** performance.now(), refreshed once a second while `running` (for countdowns). */
function useTicker(running: boolean) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (!running) return;
    setNow(performance.now());
    const timer = setInterval(() => setNow(performance.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return now;
}

const CONNECTION = {
  connecting: { label: "Connecting to controller", shape: "hollow", tone: "muted" },
  connected: { label: "Controller connected", shape: "filled", tone: "success" },
  disconnected: { label: "Controller disconnected", shape: "hollow", tone: "fault" },
} as const;

function ConnectionIndicator({ connection }: { connection: Connection }) {
  const { label, shape, tone } = CONNECTION[connection];
  return (
    <span className={styles.connection} data-connection={connection}>
      <StatusMarker shape={shape} tone={tone}>
        <span className={styles.compactLabel}>{label}</span>
      </StatusMarker>
    </span>
  );
}

export function OperatorPage() {
  const [openedAt] = useState(() => performance.now());
  const { reading, connection, refreshing, refresh } = useControllerStatus();
  const [sessionStart, setSessionStart] = useState<number | null>(null);
  // Capture this visit's first answer, ignoring any cached reading from an earlier visit.
  if (sessionStart === null && reading !== null && reading.receivedAt >= openedAt) setSessionStart(reading.status.now);
  const counting = connection === "connected" && reading !== null && (reading.status.state === "manual" || reading.status.state === "automatic");
  const now = useTicker(counting);
  const elapsed = reading ? Math.max(0, (now - reading.receivedAt) / 1000) : 0;
  const banner = experimentView({ connection, status: reading?.status ?? null, elapsed });
  return (
    <Shell
      regionLabel="Operator"
      surface="Operator"
      status={<ConnectionIndicator connection={connection} />}
      nav={
        <CrossAppLink target="store" className={styles.storefront}>
          <span className={styles.compactLabel}>Storefront</span>
        </CrossAppLink>
      }
    >
      <ExperimentBanner banner={banner} refreshing={refreshing} onRefresh={refresh} />
      <div className={styles.monitoring}>
        <ServiceHealth disconnected={connection === "disconnected"} />
        <SessionActivity sessionStart={sessionStart} />
      </div>
      <PlannedEnhancements />
    </Shell>
  );
}
