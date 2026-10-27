// @vitest-environment node
// The core checkout safety rule (approved P4 plan): a POST /api/checkout response is definite
// only if it carries a CheckoutResult for this attempt reference. 201 and 202 carry it in the
// body; definite failures carry it in the error envelope's `details`. Everything else is
// unknown and must be checked later with the same reference (E1-E3, U1, U6).
import { describe, expect, it } from "vitest";
import { classifyCheckout, type CheckoutResult } from "../src/store/checkout/outcome.ts";

const REF = "ATT-1A2B-0007";

function result(outcome: CheckoutResult["outcome"], overrides: Partial<CheckoutResult> = {}): CheckoutResult {
  return {
    attempt_ref: REF,
    order_id: outcome === "confirmed" ? "order-0f8fad5b-d9cb-469f-a165-70867728950e" : null,
    order_number: outcome === "confirmed" ? "OBS-1042-7F3A" : null,
    state: { confirmed: "completed", processing: "charging", payment_failed: "payment_declined", unavailable: "not_placed", failed: "failed", abandoned: "abandoned" }[outcome],
    outcome,
    retryable: outcome === "payment_failed" || outcome === "failed" || outcome === "abandoned",
    items: [{ product_id: "sku-002", quantity: 2, unit_price_cents: 1800 }],
    total_cents: 3600,
    error_code: null,
    unavailable_items: null,
    created_at: "2026-10-27T10:00:00Z",
    ...overrides,
  };
}

const envelope = (error_code: string, details?: unknown) => ({
  error_code,
  message: "Checkout was not completed",
  request_id: "web-1",
  trace_id: "abc",
  ...(details === undefined ? {} : { details }),
});

const classify = (status: number, body: unknown) => classifyCheckout({ status, body }, REF);

describe("definite outcomes (E1)", () => {
  it("201 with a confirmed result for this reference is a confirmed order", () => {
    const body = result("confirmed");
    expect(classify(201, body)).toEqual({ kind: "definite", result: body });
  });

  it("202 with a processing result is processing", () => {
    const body = result("processing");
    expect(classify(202, body)).toEqual({ kind: "definite", result: body });
  });

  it.each([
    [402, "payment_declined", "payment_failed"],
    [503, "payment_unavailable", "payment_failed"],
    [409, "insufficient_stock", "unavailable"],
    [409, "attempt_abandoned", "abandoned"],
    [503, "checkout_failed", "failed"],
    [500, "order_persistence_error", "failed"],
  ] as const)("%i %s with the result in details is %s", (status, code, outcome) => {
    const details = result(outcome, { error_code: code });
    expect(classify(status, envelope(code, details))).toEqual({ kind: "definite", result: details });
  });
});

describe("unknown outcomes (E2)", () => {
  it("a network failure or timeout is unknown", () => {
    expect(classifyCheckout({ networkError: true }, REF)).toEqual({ kind: "unknown", reason: "network" });
  });

  it.each([
    [502, envelope("dependency_unavailable")],
    [504, envelope("dependency_timeout")],
    [422, envelope("validation_error")],
    [409, envelope("idempotency_conflict")],
    [409, envelope("attempt_abandoned")],
    [500, envelope("internal_error")],
    [503, envelope("payment_unavailable")],
    [429, envelope("rate_limited")],
  ])("%i without details is unknown", (status, body) => {
    expect(classify(status, body).kind).toBe("unknown");
  });

  it.each([
    ["no body", 201, undefined],
    ["text", 201, "<html>proxy</html>"],
    ["a list", 201, [result("confirmed")]],
    ["details that are not a result", 402, envelope("payment_declined", { reason: "card" })],
    ["a result missing fields", 201, { attempt_ref: REF, outcome: "confirmed" }],
    ["an unknown outcome word", 201, result("confirmed", { outcome: "shipped" as CheckoutResult["outcome"] })],
    ["a confirmed result with no order number", 201, result("confirmed", { order_number: null })],
    ["200 instead of 201", 200, result("confirmed")],
    ["items that are not order lines", 201, result("confirmed", { items: [{ product_id: "sku-001" } as CheckoutResult["items"][number]] })],
    ["a negative quantity", 201, result("confirmed", { items: [{ product_id: "sku-001", quantity: -1, unit_price_cents: 100 }] })],
  ])("a malformed answer (%s) is unknown", (_label, status, body) => {
    expect(classify(status, body).kind).toBe("unknown");
  });

  it.each([
    [201, result("processing")],
    [202, result("confirmed")],
    [402, envelope("payment_declined", result("confirmed"))],
    [409, envelope("insufficient_stock", result("payment_failed"))],
    [503, envelope("checkout_failed", result("unavailable"))],
  ])("a status that contradicts the result's outcome (%i) is unknown", (status, body) => {
    expect(classify(status, body).kind).toBe("unknown");
  });
});

describe("the result must be for this attempt (E3)", () => {
  it.each([
    [201, result("confirmed", { attempt_ref: "ATT-1A2B-0006" })],
    [202, result("processing", { attempt_ref: "ATT-FFFF-0007" })],
    [402, envelope("payment_declined", result("payment_failed", { attempt_ref: "ATT-1A2B-0008" }))],
    [409, envelope("insufficient_stock", result("unavailable", { attempt_ref: "att-1a2b-0007" }))],
  ])("%i with another reference's result is unknown", (status, body) => {
    expect(classify(status, body)).toEqual({ kind: "unknown", reason: "other_attempt" });
  });
});
