// The Operator Dashboard reads live state and sends actions only on an explicit press.
import { useEffect, useRef, useState } from "react";
import { CrossAppLink } from "../shared/CrossAppLink.tsx";
import { Shell } from "../shared/Shell.tsx";
import { StatusMarker } from "../shared/ui/StatusMarker.tsx";
import { Notice } from "../shared/ui/Notice.tsx";
import { SessionActivity } from "./activity/SessionActivity.tsx";
import { CycleSection } from "./cycle/CycleSection.tsx";
import { ServiceHealth } from "./health/ServiceHealth.tsx";
import type { ActionOutcome } from "./manual/fault-actions.ts";
import { ManualFaultSection } from "./manual/ManualFaultSection.tsx";
import { useFaultActions } from "./manual/use-fault-actions.ts";
import { StopBar } from "./mobile/StopBar.tsx";
import { stopBar } from "./mobile/stop-bar.ts";
import { usePhoneLayout } from "./mobile/use-phone-layout.ts";
import styles from "./Operator.module.css";
import { PlannedEnhancements } from "./PlannedEnhancements.tsx";
import { useControllerStatus } from "./status/controller-status.ts";
import { ExperimentBanner } from "./status/ExperimentBanner.tsx";
import type { StopActions } from "./status/ExperimentStopButton.tsx";
import { experimentView, STALE_AFTER_SECONDS, type Connection } from "./status/experiment.ts";

/** performance.now(), refreshed once a second while connected (countdowns and freshness). */
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
  const phone = usePhoneLayout();
  const [openedAt] = useState(() => performance.now());
  const { reading, connection, refreshing, refresh } = useControllerStatus();
  const currentReading = useRef(reading);
  currentReading.current = reading;
  const isFresh = () => currentReading.current !== null
    && performance.now() - currentReading.current.receivedAt <= STALE_AFTER_SECONDS * 1000;
  const actions = useFaultActions({ refresh });
  const heading = useRef<HTMLHeadingElement>(null);
  const [answeredControl, setAnsweredControl] = useState<{ control: HTMLButtonElement; confirmed: boolean } | null>(null);
  const [dismissedStop, setDismissedStop] = useState<ActionOutcome | null>(null);
  const stopOutcome = actions.stopOutcome !== dismissedStop ? actions.stopOutcome : null;
  // Restore focus after the read-back renders; a confirmed press also hands off to the title.
  useEffect(() => {
    if (!answeredControl) return;
    const { control, confirmed } = answeredControl;
    if (document.activeElement === document.body || (confirmed && (document.activeElement === control
      || document.activeElement?.hasAttribute("data-focus-moved")))) {
      heading.current?.focus({ preventScroll: true });
    }
    setAnsweredControl(null);
  }, [answeredControl]);
  const rememberOutcome = (outcome: ActionOutcome | undefined, control: HTMLButtonElement) => {
    if (outcome) setAnsweredControl({ control, confirmed: outcome.kind === "confirmed" });
  };
  const [sessionStart, setSessionStart] = useState<number | null>(null);
  // Capture this visit's first answer, ignoring any cached reading from an earlier visit.
  if (sessionStart === null && reading !== null && reading.receivedAt >= openedAt) setSessionStart(reading.status.now);
  const now = useTicker(connection === "connected");
  const elapsed = reading ? Math.max(0, (now - reading.receivedAt) / 1000) : 0;
  const stale = connection === "connected" && elapsed > STALE_AFTER_SECONDS;
  const banner = experimentView({ connection, status: reading?.status ?? null, elapsed });
  const bar = phone ? stopBar(banner) : null;
  const stopActions: StopActions = {
    busy: actions.busy,
    onUnavailablePress: (control) => {
      void actions.refreshIfStale(banner.stop?.kind === "stop-cycle" ? "cycle-stop" : "stop").then((outcome) => rememberOutcome(outcome, control));
    },
    onStopCycle: (control) => {
      void actions.stopCycle().then((outcome) => rememberOutcome(outcome, control));
    },
    onStop: (scenario, control) => {
      void actions.stop(scenario).then((outcome) => rememberOutcome(outcome, control));
    },
  };
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
      <ExperimentBanner banner={banner} refreshing={refreshing} onRefresh={refresh} headingRef={heading} {...stopActions} />
      {stopOutcome ? <Notice key={`${stopOutcome.kind}-${stopOutcome.message}`} live
        icon={stopOutcome.kind === "confirmed" ? "info" : "ring"}>{stopOutcome.message}</Notice> : null}
      <div className={styles.controls}>
        <ManualFaultSection connection={connection} status={reading?.status ?? null} stale={stale} busy={actions.busy}
          onUnavailablePress={(control) => {
            void actions.refreshIfStale("start").then((outcome) => rememberOutcome(outcome, control));
          }}
          outcome={actions.startOutcome} onEdit={() => {
            actions.clearStart();
            setDismissedStop(actions.stopOutcome);
          }} onStart={(request, control) => {
            void actions.start(request, isFresh).then((outcome) => rememberOutcome(outcome, control));
          }} />
        <CycleSection connection={connection} status={reading?.status ?? null} stale={stale} busy={actions.busy}
          outcome={actions.cycleStartOutcome} onEdit={() => {
            actions.clearCycleStart();
            setDismissedStop(actions.stopOutcome);
          }} onUnavailablePress={(control) => {
            void actions.refreshIfStale("cycle-start").then((outcome) => rememberOutcome(outcome, control));
          }} onStart={(request, control) => {
            void actions.startCycle(request, isFresh).then((outcome) => rememberOutcome(outcome, control));
          }} />
      </div>
      <div className={styles.monitoring}>
        <ServiceHealth disconnected={connection === "disconnected"} />
        <SessionActivity sessionStart={sessionStart} />
      </div>
      <PlannedEnhancements />
      {bar ? <StopBar bar={bar} {...stopActions} /> : null}
    </Shell>
  );
}
