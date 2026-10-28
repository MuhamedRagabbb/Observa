// My Orders rows: the server's checkouts (GET /api/orders) merged with this browser's
// unconfirmed attempts by attempt reference. One row per reference, newest first.
// - The server's answer wins: a local record for a reference the server has a definite result
//   for is shown with that result (and settled separately, see flow.reconcile).
// - An attempt the server does not list (never recorded, or older than the 50 it returns) is
//   shown from the local record as Status unknown or Processing, so it can always be checked (U8).
// - An attempt Check status found was never placed is not listed by the server either (it has no
//   order). While nothing else describes it, this browser's reminder shows it as Not placed, an
//   attempt and never an order.
import type { CartLine } from "../cart/cart-store.ts";
import type { AttemptRecord } from "../checkout/attempts.ts";
import type { ClosedAttempt } from "../checkout/closed.ts";
import type { CheckoutResult } from "../checkout/outcome.ts";

export type OrderStatus = "confirmed" | "processing" | "unknown" | "declined" | "payment_failed" | "not_placed" | "failed";
export type OrderItem = { product_id: string; quantity: number; unit_price_cents: number | null };

export type OrderRow = {
  attemptRef: string;
  /** What the customer sees: the order number when there is one, otherwise the attempt reference. */
  reference: string;
  /** An order (confirmed or still processing) or an attempt (unconfirmed, or not placed). */
  kind: "order" | "attempt";
  status: OrderStatus;
  createdAt: string | null;
  items: OrderItem[];
  units: number;
  /** From the server; null when the server has no total (unrecorded or fenced attempts). */
  totalCents: number | null;
  result: CheckoutResult | null;
  /** This browser's unconfirmed record for the reference, if it still holds one. */
  record: AttemptRecord | null;
  /** This browser's reminder that the attempt was never placed: set only when no server result or held record describes it. */
  note: ClosedAttempt | null;
};

export function statusOf(result: CheckoutResult): OrderStatus {
  switch (result.outcome) {
    case "confirmed":
      return "confirmed";
    case "processing":
      return "processing";
    case "payment_failed":
      return result.error_code === "payment_declined" ? "declined" : "payment_failed";
    case "unavailable":
    case "abandoned":
      return "not_placed";
    default:
      return "failed";
  }
}

const unitsOf = (items: readonly { quantity: number }[]) => items.reduce((sum, item) => sum + item.quantity, 0);

export function rowFromResult(result: CheckoutResult, record: AttemptRecord | null): OrderRow {
  const status = statusOf(result);
  const items = result.items.map(({ product_id, quantity, unit_price_cents }) => ({ product_id, quantity, unit_price_cents: unit_price_cents ?? null }));
  return {
    attemptRef: result.attempt_ref,
    reference: result.order_number ?? result.attempt_ref,
    kind: status === "confirmed" || status === "processing" ? "order" : "attempt",
    status,
    createdAt: result.created_at ?? null,
    items,
    units: unitsOf(items),
    totalCents: result.total_cents ?? null,
    result,
    record,
    note: null,
  };
}

function fromLocal(record: AttemptRecord): OrderRow {
  const items = record.lines.map(({ product_id, quantity }: CartLine) => ({ product_id, quantity, unit_price_cents: null }));
  return {
    attemptRef: record.attempt_ref,
    reference: record.attempt_ref,
    kind: "attempt",
    status: record.status === "processing" ? "processing" : "unknown",
    createdAt: record.created_at || null,
    items,
    units: unitsOf(items),
    totalCents: null,
    result: null,
    record,
    note: null,
  };
}

function fromNote(note: ClosedAttempt): OrderRow {
  const items = note.lines.map(({ product_id, quantity }: CartLine) => ({ product_id, quantity, unit_price_cents: null }));
  return {
    attemptRef: note.attempt_ref,
    reference: note.attempt_ref,
    kind: "attempt",
    status: "not_placed",
    createdAt: note.created_at,
    items,
    units: unitsOf(items),
    totalCents: null,
    result: null,
    record: null,
    note,
  };
}

const time = (row: OrderRow) => (row.createdAt ? Date.parse(row.createdAt) || 0 : 0);

export function mergeOrders(server: readonly CheckoutResult[], local: readonly AttemptRecord[], closed: readonly ClosedAttempt[] = []): OrderRow[] {
  const held = new Map(local.map((record) => [record.attempt_ref, record]));
  const rows = new Map<string, OrderRow>();
  for (const result of server) {
    if (!rows.has(result.attempt_ref)) rows.set(result.attempt_ref, rowFromResult(result, held.get(result.attempt_ref) ?? null));
  }
  for (const record of local) if (!rows.has(record.attempt_ref)) rows.set(record.attempt_ref, fromLocal(record));
  for (const note of closed) if (!rows.has(note.attempt_ref)) rows.set(note.attempt_ref, fromNote(note));
  return [...rows.values()].sort((a, b) => time(b) - time(a));
}
