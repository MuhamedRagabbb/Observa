// The experiment banner (top of every Operator artboard): what the controller is doing now, its
// countdown on the controller's clock, and the banner's read-only action (Retry connection, Check
// status). Fault actions (Stop fault, Stop cycle, Stop again) arrive with the controls (P5-B, C).
import { useEffect, useRef, useState } from "react";
import { Button } from "../../shared/ui/Button.tsx";
import styles from "../Operator.module.css";
import type { Banner } from "./experiment.ts";

type Props = { banner: Banner; refreshing: boolean; onRefresh: () => Promise<void> };

export function ExperimentBanner({ banner, refreshing, onRefresh }: Props) {
  const heading = useRef<HTMLHeadingElement>(null);
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
      {banner.action ? (
        <div className={styles.bannerAction}>
          <Button
            icon="refresh"
            size="medium"
            loading={refreshing}
            onClick={() => {
              pressed.current = true;
              void onRefresh().finally(() => setTimeout(() => (pressed.current = false), 0));
            }}
          >
            {refreshing ? (banner.action === "retry" ? "Connecting…" : "Checking…") : label}
          </Button>
        </div>
      ) : null}
      <p className="visually-hidden" role="status" data-testid="experiment-announcer">
        {said}
      </p>
    </section>
  );
}
