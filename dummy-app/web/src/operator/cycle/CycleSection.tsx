import { useEffect, useId, useRef, useState } from "react";
import { Button } from "../../shared/ui/Button.tsx";
import { Icon } from "../../shared/ui/Icon.tsx";
import { Notice } from "../../shared/ui/Notice.tsx";
import { FAULT_CONTRACT } from "../fault-contract.ts";
import type { ActionOutcome } from "../manual/fault-actions.ts";
import { blockKeyRepeat } from "../manual/no-key-repeat.ts";
import type { FaultActionBusy } from "../manual/use-fault-actions.ts";
import operatorStyles from "../Operator.module.css";
import { SCENARIO_LABELS } from "../scenarios.ts";
import { Disclosure } from "../mobile/Disclosure.tsx";
import { cycleSummary, lockedSummary } from "../mobile/summaries.ts";
import type { Connection, ControllerStatus } from "../status/experiment.ts";
import { defaultCycleForm, validateCycle, type CycleForm, type CycleRequest } from "./cycle-form.ts";
import { cycleView } from "./cycle-view.ts";
import styles from "./Cycle.module.css";
import { CycleStepper } from "./CycleStepper.tsx";
import { formatDuration, plannedSchedule, scheduleFromAutoConfig } from "./schedule.ts";
import { ScheduleBar } from "./ScheduleBar.tsx";
import { Switch } from "./Switch.tsx";

const FIELDS = [
  { name: "warmup", label: "Warmup" },
  { name: "duration", label: "Fault duration" },
  { name: "recovery", label: "Recovery" },
  { name: "repeat", label: "Repeats" },
] as const;
const ERROR_ORDER = ["warmup", "duration", "recovery", "repeat", "scenarios", "seed"] as const;

export function CycleSection({ connection, status, stale = false, busy, outcome, onStart, onEdit, onUnavailablePress }: {
  connection: Connection;
  status: ControllerStatus | null;
  stale?: boolean;
  busy: FaultActionBusy;
  outcome: ActionOutcome | null;
  onStart: (request: CycleRequest, control: HTMLButtonElement) => void;
  onEdit: () => void;
  onUnavailablePress: (control: HTMLButtonElement) => void;
}) {
  const id = useId();
  const section = useRef<HTMLElement>(null);
  const [draft, setDraft] = useState(defaultCycleForm);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [invalidField, setInvalidField] = useState<string | null>(null);
  useEffect(() => {
    if (!invalidField) return;
    // Describe the error before focusing its text field or the first scenario checkbox.
    section.current?.querySelector<HTMLInputElement>(`input[name="${invalidField}"]`)?.focus();
    setInvalidField(null);
  }, [invalidField]);
  const view = cycleView({ connection, status, draft, stale });
  const locked = view.locked || busy !== null;
  const form = view.form;
  const shownErrors = form === draft ? errors : {};
  const validation = validateCycle(form);
  // Scenario selection can be empty while editing; malformed numbers cannot draw a schedule.
  const validNumbers = validation.ok || !["warmup", "duration", "recovery", "repeat"].some((name) => validation.errors[name]);
  const schedule = view.running && status?.auto?.config ? scheduleFromAutoConfig(status.auto.config) : validNumbers ? plannedSchedule({
    warmup: Number(form.warmup), duration: Number(form.duration), recovery: Number(form.recovery),
    repeat: Number(form.repeat), scenarioCount: form.scenarios.length,
  }) : null;
  const edit = <K extends keyof CycleForm,>(name: K, value: CycleForm[K]) => {
    if (locked) return;
    setDraft((current) => ({ ...current, [name]: value }));
    setErrors((current) => {
      // Keep the selection error until at least one scenario is checked.
      if (name === "scenarios" && (value as CycleForm["scenarios"]).length === 0) return current;
      const next = { ...current };
      delete next[name];
      return next;
    });
    onEdit();
  };
  return (
    <section ref={section} className={operatorStyles.monitoringSection} aria-labelledby={id}>
      <Disclosure id={id} title="Automatic cycle" index="02" locked={status !== null && status.controls.auto_start?.enabled !== true}
        defaultOpen={false} summary={lockedSummary(cycleSummary(form), view.locked)}>
      {view.locked && view.lockNotice ? <div className={styles.lockNotice}><Icon name="lock" /><span>{view.lockNotice}</span></div> : null}
      <div className={styles.stepperGrid}>
        {FIELDS.map(({ name, label }) => <CycleStepper key={name} name={name} label={label} value={form[name]}
          locked={locked} error={shownErrors[name]} onChange={(value) => edit(name, value)} />)}
      </div>
      <fieldset className={styles.scenarios} aria-describedby={shownErrors.scenarios ? `${id}-scenarios-error` : undefined}>
        <legend>Scenarios</legend>
        {FAULT_CONTRACT.scenarios.map((scenario) => (
          <label key={scenario.id} className={styles.scenario}>
            <input type="checkbox" name="scenarios" value={scenario.id} aria-label={SCENARIO_LABELS[scenario.id]}
              checked={form.scenarios.includes(scenario.id)} aria-disabled={locked || undefined}
              onChange={(event) => {
                if (locked) return;
                edit("scenarios", event.target.checked ? [...form.scenarios, scenario.id] : form.scenarios.filter((value) => value !== scenario.id));
              }} />
            <span className={styles.chip}>{scenario.code}</span><span>{SCENARIO_LABELS[scenario.id]}</span>
          </label>
        ))}
        {shownErrors.scenarios ? <p id={`${id}-scenarios-error`} className={styles.error}>{shownErrors.scenarios}</p> : null}
      </fieldset>
      <div className={styles.orderGrid}>
        <div className={styles.field}>
          <span>Order</span>
          <Switch checked={form.shuffle} locked={locked} onChange={(checked) => edit("shuffle", checked)} />
        </div>
        <div className={styles.field}>
          <label htmlFor={`${id}-seed`}>Seed</label>
          <input className={styles.seed} id={`${id}-seed`} type="text" name="seed" inputMode="numeric" autoComplete="off"
            value={form.seed} readOnly={locked} aria-disabled={locked || undefined} aria-invalid={shownErrors.seed ? true : undefined}
            aria-describedby={`${id}-seed-help${shownErrors.seed ? ` ${id}-seed-error` : ""}`}
            onChange={(event) => edit("seed", event.target.value)} />
          {shownErrors.seed ? <p id={`${id}-seed-error`} className={styles.error}>{shownErrors.seed}</p> : null}
        </div>
      </div>
      <p id={`${id}-seed-help`} className={styles.hint}>Same seed and settings repeat the same run.</p>
      <div className={styles.schedule}>
        <p className={styles.caption}>{view.progress ?? "Planned schedule"}</p>
        {schedule ? <ScheduleBar schedule={schedule} running={view.running} auto={status?.auto ?? null} /> : null}
      </div>
      <div className={styles.startRow}>
        <Button icon="refresh" unavailable={locked} loading={busy === "cycle-start"} onKeyDown={blockKeyRepeat}
          onClickCapture={(event) => {
            if (view.locked && busy === null) onUnavailablePress(event.currentTarget);
          }} onClick={(event) => {
            if (locked) return;
            const result = validateCycle(form);
            if (!result.ok) {
              setErrors(result.errors);
              setInvalidField(ERROR_ORDER.find((name) => result.errors[name]) ?? null);
              return;
            }
            setErrors({});
            onStart(result.request, event.currentTarget);
          }}>Start cycle</Button>
        <span className={styles.total}>Total {schedule ? formatDuration(schedule.totalSeconds) : "—"}</span>
      </div>
      </Disclosure>
      {outcome ? <Notice key={`${outcome.kind}-${outcome.message}`} live
        icon={outcome.kind === "confirmed" ? "info" : "ring"}>{outcome.message}</Notice> : null}
    </section>
  );
}
