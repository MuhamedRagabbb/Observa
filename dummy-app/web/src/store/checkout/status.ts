// Reading a Check status answer (GET /api/attempts/{ref}). Unlike the checkout POST, the status
// endpoint answers 200 for every state, processing included (services/orders/app.py
// get_checkout), so it needs its own rule: definite only with a matching CheckoutResult in a 200.
// A 404 counts as "nothing recorded yet" only with the backend's own attempt_not_found code:
// that is the one answer after which resolve may fence the attempt. Everything else is unknown.
import { isCheckoutResult, type CheckoutResponse, type Classification } from "./outcome.ts";

export type StatusClassification = Classification | { kind: "not_found" };

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);

export function classifyStatus(response: CheckoutResponse, attemptRef: string): StatusClassification {
  if ("networkError" in response) return { kind: "unknown", reason: "network" };
  const { status, body } = response;
  if (status === 404 && isRecord(body) && body.error_code === "attempt_not_found") return { kind: "not_found" };
  if (status !== 200 || !isCheckoutResult(body)) return { kind: "unknown", reason: "no_result" };
  if (body.attempt_ref !== attemptRef) return { kind: "unknown", reason: "other_attempt" };
  return { kind: "definite", result: body };
}
