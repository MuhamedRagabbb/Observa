import { Icon } from "../../shared/ui/Icon.tsx";
import { FAULT_CONTRACT, scenarioContract, type RangeParameter } from "../fault-contract.ts";
import { SCENARIO_LABELS } from "../scenarios.ts";
import { durationNote, type FormValues } from "./fault-form.ts";
import { HttpStatusControl } from "./HttpStatusControl.tsx";
import { NumberField } from "./NumberField.tsx";
import styles from "./ManualFault.module.css";

function rangeHint(parameter: RangeParameter) {
  if (parameter.name === "failure_rate") {
    return `${parameter.min * 100}–${parameter.max * 100}% · default ${parameter.default * 100}%. Share of payment requests that fail.`;
  }
  const range = `${parameter.min}–${parameter.max} s · default ${parameter.default} s.`;
  return parameter.name === "hold_seconds" ? `${range} How long each orders database connection is held.` : range;
}

export function ScenarioFields({ form, locked, errors, onEdit }: {
  form: FormValues; locked: boolean; errors: Record<string, string>; onEdit: (name: string, value: string) => void;
}) {
  const contract = scenarioContract(form.scenario);
  const duration = FAULT_CONTRACT.duration_seconds;
  return (
    <div className={styles.parameters}>
      <h3 className={styles.panelTitle}><span className={styles.chip}>{contract.code}</span> {SCENARIO_LABELS[form.scenario].toUpperCase()} · PARAMETERS</h3>
      {contract.parameters.map((parameter) => parameter.kind === "choice" ? (
        <HttpStatusControl key={parameter.name} parameter={parameter} value={form.settings[parameter.name] ?? ""}
          locked={locked} error={errors[parameter.name]} onChange={(value) => onEdit(parameter.name, value)} />
      ) : (
        <NumberField key={`${form.scenario}-${parameter.name}`} name={parameter.name}
          label={parameter.name === "hold_seconds" ? "Connection hold" : parameter.name === "failure_rate" ? "Failure rate" : "Added delay"}
          caption={parameter.name} value={form.settings[parameter.name] ?? ""} unit={parameter.name === "failure_rate" ? "%" : "s"}
          range={parameter.name === "failure_rate" ? { ...parameter, min: parameter.min * 100, max: parameter.max * 100, default: parameter.default * 100 } : parameter}
          hint={rangeHint(parameter)} locked={locked} error={errors[parameter.name]} onChange={(value) => onEdit(parameter.name, value)} />
      ))}
      {contract.fixed?.release_version ? (
        <div className={styles.field}>
          <div className={styles.fieldHeading}><span>Release</span><span className={styles.note}>Fixed</span></div>
          <div className={styles.fixed}><span>{contract.fixed.release_version}</span><Icon name="lock" /></div>
        </div>
      ) : null}
      <NumberField name="duration" label="Duration" value={form.duration} unit="s" caption={durationNote(form.duration)}
        hint={`${duration.min}–${duration.max} s. Stops automatically when time runs out.`}
        locked={locked} error={errors.duration} onChange={(value) => onEdit("duration", value)} />
    </div>
  );
}
