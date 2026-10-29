import { useId } from "react";
import { Icon } from "../../shared/ui/Icon.tsx";
import { stepField } from "./cycle-form.ts";
import styles from "./Cycle.module.css";

type Props = {
  name: "warmup" | "duration" | "recovery" | "repeat";
  label: string;
  value: string;
  locked: boolean;
  error?: string;
  onChange: (value: string) => void;
};

export function CycleStepper({ name, label, value, locked, error, onChange }: Props) {
  const id = useId();
  const step = (direction: -1 | 1) => stepField(name, value, direction);
  const atLimit = (direction: -1 | 1) => value.trim() !== "" && Number(value) === Number(step(direction));
  const change = (direction: -1 | 1) => {
    if (!locked && !atLimit(direction)) onChange(step(direction));
  };
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      <div className={styles.stepper}>
        <button type="button" aria-label={`Decrease ${label.toLowerCase()}`} aria-disabled={locked || atLimit(-1) || undefined}
          onClick={() => change(-1)}><Icon name="minus" /></button>
        <input id={id} name={name} type="text" inputMode={name === "repeat" ? "numeric" : "decimal"}
          autoComplete="off" value={value} readOnly={locked} aria-disabled={locked || undefined}
          aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-error` : undefined}
          onChange={(event) => { if (!locked) onChange(event.target.value); }} />
        {name !== "repeat" ? <span className={styles.unit} aria-hidden="true">s</span> : null}
        <button type="button" aria-label={`Increase ${label.toLowerCase()}`} aria-disabled={locked || atLimit(1) || undefined}
          onClick={() => change(1)}><Icon name="plus" /></button>
      </div>
      {error ? <p id={`${id}-error`} className={styles.error}>{error}</p> : null}
    </div>
  );
}
