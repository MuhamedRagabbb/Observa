import { useEffect, useId, useRef, useState } from "react";
import { Button } from "../../shared/ui/Button.tsx";
import { Icon } from "../../shared/ui/Icon.tsx";
import { Notice } from "../../shared/ui/Notice.tsx";
import { FAULT_CONTRACT, scenarioContract } from "../fault-contract.ts";
import operatorStyles from "../Operator.module.css";
import { SCENARIO_LABELS } from "../scenarios.ts";
import type { Connection, ControllerStatus } from "../status/experiment.ts";
import type { ActionOutcome } from "./fault-actions.ts";
import { defaultForm, validateForm, type ActivationRequest } from "./fault-form.ts";
import styles from "./ManualFault.module.css";
import { manualView } from "./manual-view.ts";
import { blockKeyRepeat } from "./no-key-repeat.ts";
import { ScenarioFields } from "./ScenarioFields.tsx";
import type { FaultActionBusy } from "./use-fault-actions.ts";

const DESCRIPTIONS = {
  db_connection_exhaustion: "Holds orders database connections so requests queue.",
  payments_errors: "Makes a share of payment requests fail with an HTTP error.",
  bad_deployment: `Routes the storefront to the ${scenarioContract("bad_deployment").fixed?.release_version} release with added delay.`,
};

export function ManualFaultSection({ connection, status, busy, outcome, onStart, onEdit, stale = false, onUnavailablePress }: {
  connection: Connection; status: ControllerStatus | null; busy: FaultActionBusy; outcome: ActionOutcome | null;
  stale?: boolean;
  onUnavailablePress?: (control: HTMLButtonElement) => void;
  onStart: (request: ActivationRequest, control: HTMLButtonElement) => void; onEdit: () => void;
}) {
  const id = useId();
  const section = useRef<HTMLElement>(null);
  const [draft, setDraft] = useState(() => defaultForm("payments_errors"));
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [invalidField, setInvalidField] = useState<string | null>(null);
  useEffect(() => {
    if (!invalidField) return;
    // Let the error description render before the field receives focus.
    section.current?.querySelector<HTMLInputElement>(`input[name="${invalidField}"]`)?.focus();
    setInvalidField(null);
  }, [invalidField]);
  const view = manualView({ connection, status, draft, stale });
  const locked = view.locked || busy !== null;
  const edit = (name: string, value: string) => {
    if (locked) return;
    setDraft((form) => name === "duration" ? { ...form, duration: value } : { ...form, settings: { ...form.settings, [name]: value } });
    setErrors((current) => { const next = { ...current }; delete next[name]; return next; });
    onEdit();
  };
  return (
    <section ref={section} className={operatorStyles.monitoringSection} aria-labelledby={id}>
      <div className={operatorStyles.sectionHeading}>
        <h2 id={id} className={operatorStyles.sectionTitle}>Manual fault injection</h2>
        <span className={operatorStyles.sectionIndex} aria-hidden="true">01</span>
      </div>
      {view.locked && view.lockNotice ? <div className={styles.lockNotice}><Icon name="lock" /><span>{view.lockNotice}</span></div> : null}
      <fieldset className={styles.scenarios}>
        <legend className="visually-hidden">Fault scenario</legend>
        {FAULT_CONTRACT.scenarios.map((scenario) => (
          <label key={scenario.id} className={styles.scenario}>
            <input type="radio" name="scenario" value={scenario.id} checked={view.selected === scenario.id}
              aria-label={SCENARIO_LABELS[scenario.id]} aria-describedby={`${id}-${scenario.code}`}
              aria-disabled={locked || undefined} onChange={() => {
                if (locked) return;
                setDraft((form) => ({ ...defaultForm(scenario.id), duration: form.duration }));
                setErrors({});
                onEdit();
              }} />
            <span className={styles.scenarioText}>
              <span className={styles.scenarioHeading}><span className={styles.chip}>{scenario.code}</span><span>{SCENARIO_LABELS[scenario.id]}</span>
                {view.activeScenario === scenario.id ? <span className={styles.active}>{view.activation === "acknowledged" ? "Active" : "Starting…"}</span> : null}</span>
              <span id={`${id}-${scenario.code}`} className={styles.description}>{DESCRIPTIONS[scenario.id]}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <ScenarioFields form={view.form} locked={locked} errors={view.form === draft ? errors : {}} onEdit={edit} />
      <div className={styles.inject}>
        <Button icon="play" unavailable={view.locked || (busy !== null && busy !== "start")} loading={busy === "start"}
          onKeyDown={blockKeyRepeat}
          onClickCapture={(event) => {
            if (view.locked && busy === null) onUnavailablePress?.(event.currentTarget);
          }} onClick={(event) => {
          if (locked) return;
          const result = validateForm(view.form);
          if (!result.ok) {
            setErrors(result.errors);
            setInvalidField(Object.keys(result.errors)[0] ?? null);
            return;
          }
          setErrors({});
          onStart(result.request, event.currentTarget);
        }}>Inject fault</Button>
      </div>
      {outcome ? <Notice key={`${outcome.kind}-${outcome.message}`} live icon={outcome.kind === "confirmed" ? "info" : "ring"}>{outcome.message}</Notice> : null}
    </section>
  );
}
