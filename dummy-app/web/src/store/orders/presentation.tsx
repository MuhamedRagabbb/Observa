// How an order's status reads on My Orders and order details: words first, the marker repeats them.
import { useState } from "react";
import { Button } from "../../shared/ui/Button.tsx";
import { StatusMarker } from "../../shared/ui/StatusMarker.tsx";
import type { AttemptRecord } from "../checkout/attempts.ts";
import { checkoutFlow } from "../checkout/session.ts";
import type { OrderStatus } from "./merge.ts";
import styles from "./Orders.module.css";

const MARKERS: Record<OrderStatus, { label: string; shape: "filled" | "half" | "hollow"; tone: "ink" | "muted" | "fault" }> = {
  confirmed: { label: "Confirmed", shape: "filled", tone: "ink" },
  processing: { label: "Processing", shape: "half", tone: "ink" },
  unknown: { label: "Status unknown", shape: "hollow", tone: "muted" },
  declined: { label: "Payment declined", shape: "hollow", tone: "fault" },
  payment_failed: { label: "Payment failed", shape: "hollow", tone: "fault" },
  not_placed: { label: "Not placed", shape: "hollow", tone: "muted" },
  failed: { label: "Failed", shape: "hollow", tone: "fault" },
};

export const statusLabel = (status: OrderStatus) => MARKERS[status].label;

/** Said with a never-placed attempt: finding it out changed nothing in the cart. */
export const CART_UNCHANGED = "Your cart is unchanged.";

export function OrderStatusMarker({ status }: { status: OrderStatus }) {
  const marker = MARKERS[status];
  return (
    <StatusMarker shape={marker.shape} tone={marker.tone}>
      {marker.label}
    </StatusMarker>
  );
}

/** Not placed in any form: nothing was charged. */
export const notPlaced = (status: OrderStatus) => status === "declined" || status === "payment_failed" || status === "not_placed" || status === "failed";

const dates = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });

export function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  return Number.isNaN(time) ? null : dates.format(time);
}

export const unitsLabel = (units: number) => `${units} ${units === 1 ? "item" : "items"}`;

/**
 * Check status for an attempt this browser holds: the P4-E check (GET first; resolve only after
 * 404 attempt_not_found). A failed check is said next to the button; the attempt stays.
 */
export function CheckStatusButton({
  record,
  block = false,
  size = "medium",
  onStart,
  onAnswer,
}: {
  record: AttemptRecord;
  block?: boolean;
  size?: "medium" | "large";
  /** Called when the check starts, and with whether it gave a definite answer (the row will change). */
  onStart?: () => void;
  onAnswer?: (definite: boolean) => void;
}) {
  const [state, setState] = useState<"idle" | "checking" | "failed">("idle");
  const check = async () => {
    if (state === "checking") return;
    setState("checking");
    onStart?.();
    const answer = await checkoutFlow().check(record, { quiet: true });
    setState(answer.kind === "unknown" ? "failed" : "idle");
    onAnswer?.(answer.kind === "definite");
  };
  const checking = state === "checking";
  return (
    <span className={styles.check} data-block={block || undefined}>
      <Button icon="refresh" size={size} block={block} loading={checking} unavailable={checking} onClick={() => void check()}>
        {checking ? "Checking…" : "Check status"}
      </Button>
      <span role="status" className={styles.checkNote}>
        {state === "failed" ? "Couldn’t check just now. Try again in a moment." : ""}
      </span>
    </span>
  );
}
