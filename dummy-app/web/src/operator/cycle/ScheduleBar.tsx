import type { ControllerStatus } from "../status/experiment.ts";
import { formatDuration, plannedSchedule, scheduleProgress } from "./schedule.ts";
import styles from "./Cycle.module.css";

export function ScheduleBar({ schedule, running, auto }: {
  schedule: ReturnType<typeof plannedSchedule>;
  running: boolean;
  auto: ControllerStatus["auto"];
}) {
  const progress = scheduleProgress(schedule.segments, running ? auto : null);
  if (schedule.segments === null) return <p className={styles.hint}>Too many steps to draw.</p>;
  const position = !running || !auto ? "" : auto.phase === "warmup" ? " Now: warmup phase."
    : auto.phase === "stopping" ? " Now: stopping."
    : ` Now: step ${auto.step} of ${auto.steps}, ${auto.phase} phase.`;
  return (
    <>
      <div className={styles.scheduleBar} role="img" style={{ columnGap: `min(var(--space-1), ${25 / Math.max(1, schedule.segments.length)}%)` }}
        aria-label={`Planned schedule: ${formatDuration(schedule.totalSeconds)} in total, ${schedule.steps} ${schedule.steps === 1 ? "fault" : "faults"}.${position}`}>
        {schedule.segments.map((segment, index) => (
          <span key={index} aria-hidden="true" data-kind={segment.kind}
            data-state={progress.now === index ? "now" : index < progress.done ? "done" : "planned"}
            style={{ flexGrow: segment.seconds }} />
        ))}
      </div>
      <div className={styles.legend}>
        <span><span className={styles.quietSwatch} aria-hidden="true" />Warmup · recovery</span>
        <span><span className={styles.faultSwatch} aria-hidden="true" />Fault</span>
        {running ? <span><span className={styles.nowSwatch} aria-hidden="true" />Now</span> : null}
      </div>
    </>
  );
}
