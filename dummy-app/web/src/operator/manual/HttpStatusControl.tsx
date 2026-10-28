import { useId } from "react";
import type { ChoiceParameter } from "../fault-contract.ts";
import styles from "./ManualFault.module.css";

const LABELS: Record<number, string> = { 402: "Payment Required", 503: "Service Unavailable" };

export function HttpStatusControl({ parameter, value, locked, error, onChange }: {
  parameter: ChoiceParameter; value: string; locked: boolean; error?: string; onChange: (value: string) => void;
}) {
  const id = useId();
  return (
    <div className={styles.field}>
      <div className={styles.fieldHeading}>
        <span id={id}>HTTP status</span><span className={styles.caption}>status</span>
      </div>
      <div role="radiogroup" aria-labelledby={id} className={styles.segments}
        aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-error` : undefined}>
        {parameter.choices.map((choice) => (
          <label key={choice} className={styles.segment}>
            <input type="radio" name="status_code" value={choice} checked={value === String(choice)}
              aria-disabled={locked || undefined} aria-invalid={error ? true : undefined}
              aria-describedby={error ? `${id}-error` : undefined}
              onChange={() => { if (!locked) onChange(String(choice)); }} />
            <span className={styles.segmentLabel}><span>{choice}</span> <span>{LABELS[choice]}</span></span>
          </label>
        ))}
      </div>
      {error ? <p id={`${id}-error`} className={styles.error}>{error}</p> : null}
    </div>
  );
}
