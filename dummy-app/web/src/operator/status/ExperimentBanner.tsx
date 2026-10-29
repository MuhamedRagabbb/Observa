// The experiment banner (top of every Operator artboard): what the controller is doing now, its
// countdown on the controller's clock, status reads and Stop actions.
import { useEffect, useRef, useState, type RefObject } from "react";
import { Button } from "../../shared/ui/Button.tsx";
import styles from "../Operator.module.css";
import type { Banner } from "./experiment.ts";
import { ExperimentStopButton, type StopActions } from "./ExperimentStopButton.tsx";

type Props = StopActions & {
  banner: Banner;
  refreshing: boolean;
  onRefresh: () => Promise<void>;
  headingRef?: RefObject<HTMLHeadingElement | null>;
};

export function ExperimentBanner({ banner, refreshing, onRefresh, headingRef, busy = null, onStop, onStopCycle, onUnavailablePress }: Props) {
  const ownHeading = useRef<HTMLHeadingElement>(null);
  const heading = headingRef ?? ownHeading;
  // A pressed action that goes away with the state its answer brought hands focus to the new
  // title, but only if focus went with it (nowhere else was chosen meanwhile). A press whose
  // answer changes nothing is forgotten once that answer is in.
  const pressed = useRef(false);
  useEffect(() => {
    if (!pressed.current || banner.action) return;
    pressed.current = false;
    if (document.activeElement === null || document.activeElement === document.body) heading.current?.focus({ preventScroll: true });
  }, [banner.kind, banner.action]);

  // One polite announcement per state, never the countdown. The region exists before its text.
  const [said, setSaid] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setSaid(banner.announcement), 50);
    return () => clearTimeout(timer);
  }, [banner.announcement]);

  const label = banner.action === "retry" ? "Retry connection" : "Check status";
  return (
    <section className={styles.banner} aria-label="Experiment" data-alert={banner.alert || undefined} data-kind={banner.kind}>
      <div className={styles.bannerState}>
        <p className={styles.eyebrow}>{banner.eyebrow}</p>
        <h1 ref={heading} tabIndex={-1} className={styles.bannerTitle}>
          {banner.title}
        </h1>
        <p className={styles.bannerDescription}>{banner.description}</p>
      </div>
      <div className={styles.bannerTimer}>
        {banner.timer ? (
          <>
            <p className={styles.clock}>{banner.timer.text}</p>
            <p className={styles.timerCaption}>{banner.timer.caption}</p>
            <div className={styles.progress} aria-hidden="true">
              <span className={styles.progressFill} style={{ width: `${Math.round(banner.timer.progress * 1000) / 10}%` }} />
            </div>
          </>
        ) : (
          <>
            <span className={styles.noTimer} aria-hidden="true" />
            <p className={styles.timerCaption}>{banner.timerNote}</p>
          </>
        )}
      </div>
      {banner.action || banner.stop ? (
        <div className={styles.bannerAction}>
          {banner.action ? <Button
            icon="refresh"
            size="medium"
            loading={refreshing}
            onClick={() => {
              pressed.current = true;
              void onRefresh().finally(() => setTimeout(() => (pressed.current = false), 0));
            }}
          >
            {refreshing ? (banner.action === "retry" ? "Connecting…" : "Checking…") : label}
          </Button> : null}
          {banner.stop ? <ExperimentStopButton stop={banner.stop} busy={busy} onStop={onStop}
            onStopCycle={onStopCycle} onUnavailablePress={onUnavailablePress} /> : null}
        </div>
      ) : null}
      <p className="visually-hidden" role="status" data-testid="experiment-announcer">
        {said}
      </p>
    </section>
  );
}
