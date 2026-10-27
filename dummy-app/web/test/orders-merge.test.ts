// My Orders rows (src/store/orders/merge.ts): the server's checkouts merged with this browser's
// unconfirmed attempts by attempt reference, one row per reference, newest first.
import { describe, expect, it } from "vitest";
import type { AttemptRecord } from "../src/store/checkout/attempts.ts";
import type { CheckoutResult } from "../src/store/checkout/outcome.ts";
import { mergeOrders, statusOf } from "../src/store/orders/merge.ts";

const CUSTOMER = "web-0123456789abcdefghijkl";
const LINES = [{ product_id: "sku-001", quantity: 1 }];

function server(ref: string, outcome: CheckoutResult["outcome"], extra: Partial<CheckoutResult> = {}): CheckoutResult {
  return {
    attempt_ref: ref,
    order_id: outcome === "abandoned" ? null : "order-0f8fad5b-d9cb-469f-a165-70867728950e",
    order_number: outcome === "abandoned" ? null : `OBS-10${ref.slice(-2)}-7F3A`,
    state: "x",
    outcome,
    retryable: false,
    items: outcome === "abandoned" ? [] : [{ product_id: "sku-001", quantity: 2, unit_price_cents: 1200 }],
    total_cents: outcome === "abandoned" ? null : 2400,
    error_code: null,
    unavailable_items: null,
    created_at: "2026-10-25T12:00:00Z",
    ...extra,
  };
}

const local = (ref: string, status: AttemptRecord["status"], created_at = "2026-10-26T12:00:00.000Z"): AttemptRecord => ({
  attempt_ref: ref,
  customer_id: CUSTOMER,
  lines: LINES,
  created_at,
  status,
});

describe("status labels", () => {
  it.each([
    ["confirmed", {}, "confirmed"],
    ["processing", {}, "processing"],
    ["payment_failed", { error_code: "payment_declined" }, "declined"],
    ["payment_failed", { error_code: "payment_unavailable" }, "payment_failed"],
    ["unavailable", {}, "not_placed"],
    ["abandoned", {}, "not_placed"],
    ["failed", {}, "failed"],
  ] as const)("%s %j reads as %s", (outcome, extra, status) => {
    expect(statusOf(server("ATT-1A2B-0001", outcome, extra))).toBe(status);
  });
});

describe("merging", () => {
  it("one row per reference: a server result and the local record for it make one row", () => {
    const rows = mergeOrders([server("ATT-1A2B-0001", "confirmed")], [local("ATT-1A2B-0001", "unknown")]);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ attemptRef: "ATT-1A2B-0001", status: "confirmed", reference: "OBS-1001-7F3A", kind: "order" });
    expect(rows[0]?.record?.attempt_ref).toBe("ATT-1A2B-0001");
  });

  it("an attempt the server has not recorded is a Status unknown row, from the local record (U8: whatever its age)", () => {
    const rows = mergeOrders([server("ATT-1A2B-0001", "confirmed")], [local("ATT-1A2B-0002", "unknown", "2025-01-01T00:00:00.000Z")]);
    expect(rows.map((r) => [r.attemptRef, r.status, r.reference, r.kind])).toEqual([
      ["ATT-1A2B-0001", "confirmed", "OBS-1001-7F3A", "order"],
      ["ATT-1A2B-0002", "unknown", "ATT-1A2B-0002", "attempt"],
    ]);
    expect(rows[1]).toMatchObject({ totalCents: null, items: [{ product_id: "sku-001", quantity: 1, unit_price_cents: null }] });
  });

  it("a local 'submitting' record reads as unknown; a local 'processing' one as processing", () => {
    const rows = mergeOrders([], [local("ATT-1A2B-0003", "submitting"), local("ATT-1A2B-0004", "processing", "2026-10-27T00:00:00.000Z")]);
    expect(rows.map((r) => [r.attemptRef, r.status])).toEqual([
      ["ATT-1A2B-0004", "processing"],
      ["ATT-1A2B-0003", "unknown"],
    ]);
  });

  it("the server's answer wins over a local status (the record is reconciled separately)", () => {
    const rows = mergeOrders([server("ATT-1A2B-0005", "payment_failed", { error_code: "payment_declined" })], [local("ATT-1A2B-0005", "processing")]);
    expect(rows).toMatchObject([{ status: "declined", kind: "attempt", reference: "OBS-1005-7F3A" }]);
  });

  it("an attempt fenced by resolve has no order number: the attempt reference is shown", () => {
    expect(mergeOrders([server("ATT-1A2B-0006", "abandoned")], [])).toMatchObject([
      { reference: "ATT-1A2B-0006", status: "not_placed", kind: "attempt", totalCents: null, items: [] },
    ]);
  });

  it("newest first; a duplicate server entry for one reference is shown once", () => {
    const older = server("ATT-1A2B-0007", "confirmed", { created_at: "2026-10-20T12:00:00Z" });
    const newer = server("ATT-1A2B-0008", "failed", { created_at: "2026-10-24T12:00:00Z" });
    expect(mergeOrders([older, newer, older], []).map((r) => r.attemptRef)).toEqual(["ATT-1A2B-0008", "ATT-1A2B-0007"]);
  });

  it("the row's item count is the number of units", () => {
    const result = server("ATT-1A2B-0009", "confirmed", {
      items: [
        { product_id: "sku-001", quantity: 1, unit_price_cents: 1200 },
        { product_id: "sku-002", quantity: 2, unit_price_cents: 1800 },
      ],
    });
    expect(mergeOrders([result], [])[0]?.units).toBe(3);
  });
});
