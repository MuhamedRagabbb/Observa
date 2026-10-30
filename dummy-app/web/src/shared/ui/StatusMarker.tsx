import type { ReactNode } from "react";
import styles from "./StatusMarker.module.css";

type StatusMarkerProps = {
  /** Filled = settled (In stock, Confirmed); half = in progress (Processing); hollow = not there (Out of stock, unknown, declined). */
  shape: "filled" | "half" | "hollow";
  tone: "success" | "ink" | "muted" | "fault";
  children: ReactNode;
};

/** A small dot plus the status in words. The words carry the meaning; colour and shape repeat it. */
export function StatusMarker({ shape, tone, children }: StatusMarkerProps) {
  return (
    <span className={styles.marker} data-tone={tone}>
      <span className={styles.dot} data-shape={shape} data-tone={tone} aria-hidden="true" />
      {children}
    </span>
  );
}
