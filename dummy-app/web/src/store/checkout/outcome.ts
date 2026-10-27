// The core checkout safety rule (approved P4 plan): a POST /api/checkout response is definite
// only if it carries a CheckoutResult for this attempt reference. 201 and 202 carry it in the
// body; definite failures carry it in the error envelope's `details` (services/orders/app.py
// respond, passed through by the Gateway). Anything else, including a result for another
// reference or one whose outcome contradicts the status, is unknown: the attempt may or may
// not have been placed and must be checked later with the same reference.
import type { GatewayComponents } from "../../api/gateway-client.ts";

export type CheckoutResult = GatewayComponents["schemas"]["CheckoutResult"];
export type Outcome = CheckoutResult["outcome"];

export type CheckoutResponse = { status: number; body: unknown } | { networkError: true };
export type Classification =
  | { kind: "definite"; result: CheckoutResult }
  | { kind: "unknown"; reason: "network" | "no_result" | "other_attempt" | "inconsistent" };

const OUTCOMES: readonly Outcome[] = ["confirmed", "processing", "payment_failed", "unavailable", "failed", "abandoned"];

/** Which outcome each status may carry (services/orders/app.py respond and FAILURE_HTTP). */
const STATUS_OUTCOMES: Record<number, readonly Outcome[]> = {
  201: ["confirmed"],
  202: ["processing"],
  402: ["payment_failed"],
  409: ["unavailable", "abandoned"],
  500: ["failed"],
  503: ["payment_failed", "failed"],
};

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isNullableString = (value: unknown) => value === null || typeof value === "string";

/** An order line as the result carries it: product, a positive whole quantity, a price or null. */
const isResultLine = (value: unknown) =>
  isRecord(value) &&
  typeof value.product_id === "string" &&
  Number.isInteger(value.quantity) &&
  (value.quantity as number) > 0 &&
  (value.unit_price_cents === null || value.unit_price_cents === undefined || typeof value.unit_price_cents === "number");

/** The fields this app relies on are present and of the right type. */
function isCheckoutResult(value: unknown): value is CheckoutResult {
  if (!isRecord(value)) return false;
  const outcome = value.outcome as Outcome;
  return (
    typeof value.attempt_ref === "string" &&
    typeof value.state === "string" &&
    OUTCOMES.includes(outcome) &&
    typeof value.retryable === "boolean" &&
    Array.isArray(value.items) &&
    value.items.every(isResultLine) &&
    isNullableString(value.order_id) &&
    isNullableString(value.order_number) &&
    (value.total_cents === null || typeof value.total_cents === "number") &&
    // A confirmed order always has its order number; without it there is nothing to show.
    (outcome !== "confirmed" || (typeof value.order_number === "string" && value.order_number.length > 0))
  );
}

export function classifyCheckout(response: CheckoutResponse, attemptRef: string): Classification {
  if ("networkError" in response) return { kind: "unknown", reason: "network" };
  const { status, body } = response;
  const allowed = STATUS_OUTCOMES[status];
  if (!allowed) return { kind: "unknown", reason: "no_result" };
  const candidate = status < 400 ? body : isRecord(body) ? body.details : undefined;
  if (!isCheckoutResult(candidate)) return { kind: "unknown", reason: "no_result" };
  if (candidate.attempt_ref !== attemptRef) return { kind: "unknown", reason: "other_attempt" };
  if (!allowed.includes(candidate.outcome)) return { kind: "unknown", reason: "inconsistent" };
  return { kind: "definite", result: candidate };
}
