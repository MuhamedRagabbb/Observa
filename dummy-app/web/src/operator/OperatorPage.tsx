// The Operator Dashboard (P5). P5-A: the header's controller connection and the experiment banner,
// from GET /faults/status. The fault controls, service health and activity follow in later slices.
import { useEffect, useState } from "react";
import { CrossAppLink } from "../shared/CrossAppLink.tsx";
import { Shell } from "../shared/Shell.tsx";
import { StatusMarker } from "../shared/ui/StatusMarker.tsx";
import styles from "./Operator.module.css";
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
  const { reading, connection, refreshing, refresh } = useControllerStatus();
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
    </Shell>
  );
}
