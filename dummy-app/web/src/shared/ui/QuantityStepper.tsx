import { Icon } from "./Icon.tsx";
import styles from "./QuantityStepper.module.css";

type QuantityStepperProps = {
  /** The item's display name, used in the accessible names ("Increase quantity of Coffee Mug"). */
  itemName: string;
  value: number;
  min: number;
  max: number;
  /** Called with the requested new quantity; the owner decides whether to apply it. */
  onChange: (value: number) => void;
  /** The whole stepper is inert, such as for an unavailable item. */
  disabled?: boolean;
};

/**
 * Minus, value, plus. At a limit the button reports itself unavailable but stays focusable,
 * so pressing minus down to the minimum does not throw keyboard focus back to the page.
 */
export function QuantityStepper({ itemName, value, min, max, onChange, disabled = false }: QuantityStepperProps) {
  const canDecrease = value > min;
  const canIncrease = value < max;
  // Clamped, so a value left outside the range (stock fell below it) comes straight back.
  const step = (delta: -1 | 1) => {
    if (disabled || (delta < 0 ? !canDecrease : !canIncrease)) return;
    onChange(Math.min(max, Math.max(min, value + delta)));
  };
  return (
    <div className={styles.row} role="group" aria-label={`Quantity of ${itemName}`}>
      <span className={styles.caption} aria-hidden="true">
        Qty
      </span>
      <div className={styles.stepper}>
        <button
          type="button"
          className={styles.step}
          aria-label={`Decrease quantity of ${itemName}`}
          aria-disabled={!canDecrease || undefined}
          disabled={disabled}
          onClick={() => step(-1)}
        >
          <Icon name="minus" />
        </button>
        <span className={styles.value} role="status" aria-live="polite">
          <span className="visually-hidden">Quantity </span>
          {value}
        </span>
        <button
          type="button"
          className={styles.step}
          aria-label={`Increase quantity of ${itemName}`}
          aria-disabled={!canIncrease || undefined}
          disabled={disabled}
          onClick={() => step(1)}
        >
          <Icon name="plus" />
        </button>
      </div>
    </div>
  );
}
