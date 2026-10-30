import { useId, useState } from "react";
import styles from "./ManualFault.module.css";

type Props = {
  name: string;
  label: string;
  value: string;
  unit: string;
  caption?: string | null;
  hint: string;
  range?: { min: number; max: number; default: number; integer?: boolean };
  locked: boolean;
  error?: string;
  onChange: (value: string) => void;
};

export function NumberField({ name, label, value, unit, caption, hint, range, locked, error, onChange }: Props) {
  const id = useId();
  const [lastValid, setLastValid] = useState(range?.default ?? 0);
  const number = /^\d+(\.\d{1,2})?$/.test(value) ? Number(value) : NaN;
  const valid = range && Number.isFinite(number) && number >= range.min && number <= range.max && (!range.integer || Number.isInteger(number));
  // Preserve the last slider position while a text edit is incomplete or invalid.
  if (valid && lastValid !== number) setLastValid(number);
  const describedBy = `${id}-hint${error ? ` ${id}-error` : ""}`;
  // The slider is coarse (1% or 0.1 s); the text field is the exact value sent.
  return (
    <div className={styles.field}>
      <div className={styles.fieldHeading}>
        <label htmlFor={id}>{label}</label>
        {caption ? <span className={name === "duration" ? styles.note : styles.caption}>{caption}</span> : null}
      </div>
      <div className={range ? styles.numberRow : styles.durationRow}>
        {range ? (
          <input type="range" aria-label={`${label} slider`} min={range.min} max={range.max}
            step={range.integer || range.max > 20 ? 1 : 0.1} value={valid ? number : lastValid}
            aria-disabled={locked || undefined} aria-invalid={error ? true : undefined} aria-describedby={describedBy}
            onChange={(event) => { if (!locked) onChange(event.target.value); }} />
        ) : null}
        <div className={styles.numberInput}>
          <input id={id} name={name} type="text" inputMode="decimal" autoComplete="off" value={value}
            readOnly={locked} aria-disabled={locked || undefined} aria-invalid={error ? true : undefined}
            aria-describedby={describedBy} onChange={(event) => { if (!locked) onChange(event.target.value); }} />
          <span aria-hidden="true">{unit}</span>
        </div>
      </div>
      <p id={`${id}-hint`} className={styles.hint}>{hint}</p>
      {error ? <p id={`${id}-error`} className={styles.error}>{error}</p> : null}
    </div>
  );
}
