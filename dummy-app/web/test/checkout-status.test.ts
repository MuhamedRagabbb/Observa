// Check status: GET /api/attempts/{ref} answers 200 with a CheckoutResult for every state
// (processing included) and 404 attempt_not_found when nothing is recorded
// (services/orders/app.py get_checkout). Only that exact 404 may lead to resolve; anything else
// that is not a matching result leaves the attempt unknown.
import { describe, expect, it } from "vitest";
import type { CheckoutResult } from "../src/store/checkout/outcome.ts";
import { classifyStatus } from "../src/store/checkout/status.ts";

const REF = "ATT-1A2B-0001";

function result(outcome: CheckoutResult["outcome"], extra: Partial<CheckoutResult> = {}): CheckoutResult {
  return {
    attempt_ref: REF,
    order_id: outcome === "confirmed" ? "order-0f8fad5b-d9cb-469f-a165-70867728950e" : null,
    order_number: outcome === "confirmed" ? "OBS-1042-7F3A" : null,
    state: "charging",
    outcome,
    retryable: false,
    items: [{ product_id: "sku-001", quantity: 1, unit_price_cents: 1200 }],
    total_cents: 1200,
    error_code: null,
    unavailable_items: null,
    created_at: "2026-10-27T10:00:00Z",
    ...extra,
  };
}

describe("a 200 answer", () => {
  it.each(["confirmed", "processing", "payment_failed", "unavailable", "failed", "abandoned"] as const)("with a matching %s result is definite", (outcome) => {
    expect(classifyStatus({ status: 200, body: result(outcome) }, REF)).toEqual({ kind: "definite", result: result(outcome) });
  });

  it("an abandoned result with no items or total (fenced by resolve) is definite", () => {
    const fenced = result("abandoned", { items: [], total_cents: null, order_id: null });
    expect(classifyStatus({ status: 200, body: fenced }, REF)).toEqual({ kind: "definite", result: fenced });
  });

  it("for another reference is not trusted", () => {
    expect(classifyStatus({ status: 200, body: result("confirmed", { attempt_ref: "ATT-1A2B-0002" }) }, REF)).toEqual({ kind: "unknown", reason: "other_attempt" });
  });

  it.each([
    ["no body", undefined],
    ["an array", [result("confirmed")]],
    ["a confirmed result without its order number", result("confirmed", { order_number: null })],
    ["an unknown outcome", { ...result("failed"), outcome: "lost" }],
    ["a line with a fractional quantity", result("failed", { items: [{ product_id: "sku-001", quantity: 1.5, unit_price_cents: 1 }] })],
    ["an error envelope", { error_code: "attempt_not_found", message: "x" }],
  ])("that is malformed (%s) is unknown", (_name, body) => {
    expect(classifyStatus({ status: 200, body }, REF)).toEqual({ kind: "unknown", reason: "no_result" });
  });
});

describe("other answers", () => {
  it("404 attempt_not_found means nothing is recorded yet: resolve may fence it", () => {
    expect(classifyStatus({ status: 404, body: { error_code: "attempt_not_found", message: "No checkout recorded" } }, REF)).toEqual({ kind: "not_found" });
  });

  it.each([
    ["a 404 from somewhere else", 404, { error_code: "http_error", message: "x" }],
    ["a 404 without a body", 404, undefined],
    ["422 (the Gateway refused the request)", 422, { error_code: "invalid_attempt_key", message: "x" }],
    ["201 (GET never creates)", 201, result("confirmed")],
    ["202", 202, result("processing")],
    ["409", 409, { error_code: "attempt_abandoned", message: "x", details: result("abandoned") }],
    ["500", 500, { error_code: "internal_error", message: "x" }],
    ["502", 502, { error_code: "dependency_unavailable", message: "x" }],
    ["504", 504, { error_code: "dependency_timeout", message: "x" }],
  ])("%s is unknown, never not-found", (_name, status, body) => {
    expect(classifyStatus({ status, body }, REF)).toEqual({ kind: "unknown", reason: "no_result" });
  });

  it("a network failure is unknown", () => {
    expect(classifyStatus({ networkError: true }, REF)).toEqual({ kind: "unknown", reason: "network" });
  });
});
