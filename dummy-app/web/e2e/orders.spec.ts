// P4-F: My Orders and order details, against the production build with typed mocks.
// GET /api/orders (customer-scoped, limit 50) merged with this browser's unconfirmed attempts;
// Check status goes through the P4-E flow. Safety cases from e2e/README.md: T4, U8, plus
// reconciliation (a confirmed order's cart effect happens once) and privacy.
import type { Page } from "@playwright/test";
import {
  CUSTOMER,
  envelope,
  fenced,
  gateContents,
  gateRecord,
  place,
  READY,
  result,
  seed,
  seedGate,
  serve,
  serveStatus,
  settled,
  storedCart,
  type CheckoutResult,
} from "./support/checkout.ts";
import { STORE_URL, expect, test, type MockApi, type MockReply } from "./support/harness.ts";

const ORDERS_URL = `${STORE_URL}orders`;
const ARTBOARD = {
  confirmed: result("ATT-1A2B-0001", "confirmed", { order_number: "OBS-1042-7F3A", created_at: "2026-10-25T12:00:00Z" }),
  processing: result("ATT-1A2B-0002", "processing", {
    order_number: "OBS-1039-2C11",
    created_at: "2026-10-24T12:00:00Z",
    items: [
      { product_id: "sku-003", quantity: 1, unit_price_cents: 600 },
      { product_id: "sku-005", quantity: 2, unit_price_cents: 300 },
    ],
    total_cents: 1200,
  }),
  declined: result("ATT-1A2B-0003", "payment_failed", {
    order_number: "OBS-1031-9B04",
    error_code: "payment_declined",
    created_at: "2026-10-22T12:00:00Z",
    items: [{ product_id: "sku-002", quantity: 1, unit_price_cents: 1800 }],
    total_cents: 1800,
  }),
};
const UNKNOWN = gateRecord("ATT-7F3A-0915", "unknown", [
  { product_id: "sku-001", quantity: 1 },
  { product_id: "sku-005", quantity: 2 },
]);

/** GET /api/orders; returns each request's query string. */
function serveOrders(api: MockApi, reply: () => MockReply | Promise<MockReply>) {
  const queries: string[] = [];
  api.onGateway("GET", "/api/orders", (request) => {
    queries.push(request.query.toString());
    return reply();
  });
  return queries;
}

const list = (body: CheckoutResult[]): MockReply => ({ status: 200, body });
const table = (page: Page) => page.getByRole("table", { name: "Your orders" });
const rows = (page: Page) => table(page).getByRole("row").filter({ hasNot: page.getByRole("columnheader") });

test.describe("My Orders list", () => {
  test("the artboard's four rows: confirmed, unknown (from this browser), processing, declined", async ({ page, api, capture }) => {
    serve(api, () => ({ status: 500 }));
    serveStatus(api, {});
    const queries = serveOrders(api, () => list([ARTBOARD.confirmed, ARTBOARD.processing, ARTBOARD.declined]));
    await seed(page, []);
    await seedGate(page, [{ ...UNKNOWN, created_at: "2026-10-25T13:00:00.000Z" }]);
    await page.goto(ORDERS_URL);
    await expect(rows(page)).toHaveCount(4);
    await expect(page.getByText("4 orders")).toBeVisible();
    await expect(rows(page).nth(0)).toContainText("ATT-7F3A-0915");
    await expect(rows(page).nth(0)).toContainText("Status unknown");
    await expect(rows(page).nth(0).getByRole("button", { name: "Check status" })).toBeVisible();
    await expect(rows(page).nth(1)).toContainText("OBS-1042-7F3A");
    await expect(rows(page).nth(1)).toContainText("Confirmed");
    await expect(rows(page).nth(2)).toContainText("Processing");
    await expect(rows(page).nth(3)).toContainText("Payment declined");
    await expect(rows(page).nth(3)).toContainText("Not charged");
    expect(queries).toEqual([`customer_id=${CUSTOMER}&limit=50`]);
    await settled(page);
    await capture("orders-list");
  });

  // GET /api/orders lists only checkouts that have an order, so a fenced attempt is never in it
  // (services/shared/attempts.py list: order_id IS NOT NULL). A sold-out attempt has one.
  test("closed attempts the server lists: not placed (sold out) and failed, each with an order number", async ({ page, api, capture }) => {
    serveOrders(api, () =>
      list([
        result("ATT-1A2B-0011", "unavailable", { order_number: "OBS-1050-1A2B", unavailable_items: [{ product_id: "sku-004", requested: 1, available: 0 }] }),
        result("ATT-1A2B-0012", "failed", { order_number: "OBS-1049-3C4D", created_at: "2026-10-20T12:00:00Z" }),
      ]),
    );
    await seed(page, []);
    await page.goto(ORDERS_URL);
    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).filter({ hasText: "OBS-1050-1A2B" })).toContainText("Not placed");
    await expect(rows(page).filter({ hasText: "OBS-1049-3C4D" })).toContainText("Failed");
    await settled(page);
    await capture("orders-list-closed-attempts");
  });

  test("empty", async ({ page, api, capture }) => {
    serveOrders(api, () => list([]));
    await seed(page, []);
    await page.goto(ORDERS_URL);
    await expect(page.getByRole("heading", { level: 2, name: "No orders yet." })).toBeVisible();
    await expect(page.getByRole("link", { name: "Shop the collection" })).toBeVisible();
    await settled(page);
    await capture("orders-empty");
  });

  test("loading", async ({ page, api, capture }) => {
    serveOrders(api, () => new Promise<MockReply>(() => undefined));
    await seed(page, []);
    await page.goto(ORDERS_URL);
    await expect(page.getByTestId("orders-loading")).toBeVisible();
    await settled(page);
    await capture("orders-loading");
  });

  test("error, then Try again", async ({ page, api, capture }) => {
    let fail = true;
    serveOrders(api, () => (fail ? { status: 502, body: envelope("dependency_unavailable") } : list([ARTBOARD.confirmed])));
    await seed(page, []);
    await page.goto(ORDERS_URL);
    await expect(page.getByRole("heading", { level: 2, name: "We couldn’t load your orders." })).toBeVisible();
    await settled(page);
    await capture("orders-error");
    fail = false;
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(rows(page)).toHaveCount(1);
  });

  test("50 orders: the notice, and an older unconfirmed attempt still listed and checkable (U8)", async ({ page, api, capture }) => {
    const fifty = Array.from({ length: 50 }, (_, i) =>
      result(`ATT-1A2B-${String(i + 100).padStart(4, "0")}`, "confirmed", { order_number: `OBS-${2000 + i}-7F3A`, created_at: "2026-10-25T12:00:00Z" }),
    );
    serve(api, () => ({ status: 500 }));
    const status = serveStatus(api, { get: (ref) => ({ status: 200, body: result(ref, "confirmed", { order_number: "OBS-0999-0A0B" }) }) });
    serveOrders(api, () => list(fifty));
    await seed(page, READY);
    await seedGate(page, [{ ...gateRecord("ATT-0000-0001", "unknown"), created_at: "2025-01-02T12:00:00.000Z" }]);
    await page.goto(ORDERS_URL);
    await expect(page.getByText("Showing your 50 most recent orders")).toBeVisible();
    await expect(rows(page)).toHaveCount(51);
    // Off-screen thumbnails load lazily: only the fonts matter for this viewport capture.
    await page.evaluate(() => document.fonts.ready);
    await capture("orders-limit-notice", "viewport");
    const old = rows(page).filter({ hasText: "ATT-0000-0001" });
    await old.getByRole("button", { name: "Check status" }).click();
    await expect.poll(async () => (await gateContents(page)).attempts).toEqual([]);
    expect(status.seen).toEqual(["GET ATT-0000-0001"]);
    expect(await storedCart(page)).toEqual([]);
  });

  test("reached from the header at every width", async ({ page, api }) => {
    serve(api, () => ({ status: 500 }));
    serveOrders(api, () => list([]));
    await seed(page, []);
    await page.goto(STORE_URL);
    await page.getByRole("banner").getByRole("link", { name: "My Orders" }).click();
    await expect(page).toHaveURL(ORDERS_URL);
    await expect(page.getByRole("heading", { level: 1, name: "My Orders" })).toBeVisible();
  });
});

test.describe("order details", () => {
  test("confirmed", async ({ page, api, capture }) => {
    serveOrders(api, () => list([ARTBOARD.confirmed]));
    await seed(page, []);
    await page.goto(ORDERS_URL);
    await page.getByRole("link", { name: "View order OBS-1042-7F3A" }).click();
    await expect(page).toHaveURL(`${ORDERS_URL}/OBS-1042-7F3A`);
    await expect(page.getByRole("heading", { level: 1, name: "Order" })).toBeVisible();
    await expect(page.getByText("Demo order. No payment was taken and nothing will be shipped.")).toBeVisible();
    await expect(page.getByTestId("order-total")).toContainText("$54.00");
    await settled(page);
    await capture("order-detail-confirmed");
  });

  test("status unknown: Check status (GET first) shows the answer; the cart loses the units once", async ({ page, api, capture }) => {
    serve(api, () => ({ status: 500 }));
    const status = serveStatus(api, { get: (ref) => ({ status: 200, body: result(ref, "confirmed", { order_number: "OBS-1060-9F9F" }) }) });
    let recorded = false;
    serveOrders(api, () => list(recorded ? [result(UNKNOWN.attempt_ref, "confirmed", { order_number: "OBS-1060-9F9F" })] : []));
    await seed(page, [...UNKNOWN.lines, { product_id: "sku-003", quantity: 1 }]);
    await seedGate(page, [UNKNOWN]);
    await page.goto(`${ORDERS_URL}/ATT-7F3A-0915`);
    await expect(page.getByRole("heading", { level: 1, name: "Order attempt" })).toBeVisible();
    await expect(page.getByText(/We couldn’t confirm whether this order was placed/)).toBeVisible();
    await settled(page);
    await capture("order-detail-unknown");
    recorded = true;
    await page.getByRole("button", { name: "Check status" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Order" })).toBeVisible();
    expect(status.seen).toEqual(["GET ATT-7F3A-0915"]);
    expect(await storedCart(page)).toEqual([{ product_id: "sku-003", quantity: 1 }]);
    // A reload replays the journal: nothing more comes out.
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "Order" })).toBeVisible();
    expect(await storedCart(page)).toEqual([{ product_id: "sku-003", quantity: 1 }]);
  });

  test("payment declined", async ({ page, api, capture }) => {
    serveOrders(api, () => list([ARTBOARD.declined]));
    await seed(page, []);
    await page.goto(`${ORDERS_URL}/OBS-1031-9B04`);
    await expect(page.getByRole("heading", { level: 1, name: "Order attempt" })).toBeVisible();
    await expect(page.getByText(/wasn’t placed and nothing was charged/)).toBeVisible();
    await settled(page);
    await capture("order-detail-declined");
  });

  test("processing", async ({ page, api, capture }) => {
    serveOrders(api, () => list([ARTBOARD.processing]));
    await seed(page, []);
    await page.goto(`${ORDERS_URL}/OBS-1039-2C11`);
    await expect(page.getByRole("heading", { level: 1, name: "Order" })).toBeVisible();
    await expect(page.getByText("Processing")).toBeVisible();
    await settled(page);
    await capture("order-detail-processing");
  });
});

test.describe("other tabs and safety", () => {
  test("an unconfirmed attempt appears in another tab's My Orders, and resolving it there updates both (T4)", async ({ page, api, context }) => {
    serve(api, () => "network-error");
    const status = serveStatus(api, {
      get: () => ({ status: 404, body: { error_code: "attempt_not_found", message: "x" } }),
      resolve: (ref) => ({ status: 409, body: envelope("attempt_abandoned", fenced(ref)) }),
    });
    // The real list never has a fenced attempt: both tabs learn it from the browser's reminder.
    serveOrders(api, () => list([]));
    await seed(page, READY);
    await seedGate(context, [UNKNOWN]);
    await page.goto(ORDERS_URL);
    const other = await context.newPage();
    await other.goto(ORDERS_URL);
    for (const tab of [page, other]) await expect(rows(tab).filter({ hasText: UNKNOWN.attempt_ref })).toContainText("Status unknown");
    await other.getByRole("button", { name: "Check status" }).click();
    for (const tab of [page, other]) {
      const row = rows(tab).filter({ hasText: UNKNOWN.attempt_ref });
      await expect(row).toContainText("Not placed");
      await expect(row).toContainText("Your cart is unchanged.");
      await expect(row.getByRole("button", { name: "Check status" })).toHaveCount(0);
    }
    expect(status.seen).toEqual([`GET ${UNKNOWN.attempt_ref}`, `RESOLVE ${UNKNOWN.attempt_ref}`]);
    await other.close();
  });

  test("the list settles a held attempt it shows as confirmed: the cart loses the units once, also after reloads", async ({ page, api }) => {
    const status = serveStatus(api, {});
    serveOrders(api, () => list([result(UNKNOWN.attempt_ref, "confirmed", { order_number: "OBS-1061-1111" })]));
    await seed(page, [...UNKNOWN.lines, { product_id: "sku-002", quantity: 1 }]);
    await seedGate(page, [UNKNOWN]);
    await page.goto(ORDERS_URL);
    await expect(rows(page).filter({ hasText: "OBS-1061-1111" })).toContainText("Confirmed");
    await expect.poll(async () => (await gateContents(page)).attempts).toEqual([]);
    expect(await storedCart(page)).toEqual([{ product_id: "sku-002", quantity: 1 }]);
    for (let i = 0; i < 2; i++) {
      await page.reload();
      await expect(rows(page)).toHaveCount(1);
      expect(await storedCart(page)).toEqual([{ product_id: "sku-002", quantity: 1 }]);
    }
    expect(status.seen).toEqual([]);
  });

  test("privacy: My Orders asks by customer ID and limit only; nothing personal is stored or in the address", async ({ page, api }) => {
    serve(api, (ref) => ({ status: 201, body: result(ref, "confirmed") }));
    serveStatus(api, { get: (ref) => ({ status: 200, body: result(ref, "confirmed") }) });
    const queries = serveOrders(api, () => list([ARTBOARD.confirmed]));
    await seed(page, READY);
    await page.goto(`${STORE_URL}checkout`);
    await page.getByLabel("Full name").fill("Demo Name");
    await page.getByLabel("Email").fill("demo@example.com");
    await page.getByRole("button", { name: /^Place Order/ }).click();
    await page.getByRole("link", { name: "View My Orders" }).click();
    await expect(rows(page)).toHaveCount(1);
    expect(queries).toEqual([`customer_id=${CUSTOMER}&limit=50`]);
    const held = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage }, url: location.href }));
    const everything = held + JSON.stringify(await gateContents(page));
    for (const secret of ["Demo Name", "demo@example.com"]) expect(everything).not.toContain(secret);
  });
});

// Post-P4-I: Check status finds an attempt was never placed. The server fences the reference but
// never lists it (it has no order), so the browser keeps a one-day reminder (checkout/closed.ts):
// the row changes in place to Not placed, says the cart is unchanged and offers Try again.
test.describe("never placed: the reminder in this browser", () => {
  const FENCED = UNKNOWN.attempt_ref;
  const announcer = (page: Page) => page.getByTestId("orders-announcer");
  const tryAgain = (page: Page) => page.getByRole("link", { name: "Try again" });
  const checkStatus = (page: Page) => rows(page).filter({ hasText: FENCED }).getByRole("button", { name: "Check status" });

  /** The Gateway for a browser holding one unknown attempt: not found, then fenced by resolve. */
  function fencing(api: MockApi) {
    const refs = serve(api, (ref) => ({ status: 201, body: result(ref, "confirmed") }));
    const status = serveStatus(api, {
      get: () => ({ status: 404, body: { error_code: "attempt_not_found", message: "x" } }),
      resolve: (ref) => ({ status: 409, body: envelope("attempt_abandoned", fenced(ref)) }),
    });
    serveOrders(api, () => list([]));
    return { refs, status };
  }

  test("the row changes in place: Not placed, 'Your cart is unchanged.', Try again; focus and announcement follow; the cart is untouched", async ({ page, api, capture }) => {
    const { status } = fencing(api);
    await seed(page, READY);
    await seedGate(page, [UNKNOWN]);
    await page.goto(ORDERS_URL);
    await checkStatus(page).click();
    const row = rows(page).filter({ hasText: FENCED });
    await expect(row).toContainText("Not placed");
    await expect(row).toContainText("Your cart is unchanged.");
    await expect(row).toContainText("Attempt · not placed");
    await expect(row.getByText(/OBS-/)).toHaveCount(0);
    await expect(row.getByRole("button", { name: "Check status" })).toHaveCount(0);
    await expect(tryAgain(page)).toBeFocused();
    await expect(announcer(page)).toHaveText(`${FENCED}: Not placed. Your cart is unchanged.`);
    expect(status.seen).toEqual([`GET ${FENCED}`, `RESOLVE ${FENCED}`]);
    expect(await storedCart(page)).toEqual(READY);
    expect((await gateContents(page)).attempts).toEqual([]);
    await expect(page.getByText("1 order", { exact: true })).toBeVisible();
    await settled(page);
    await capture("orders-list-not-placed");
  });

  test("it survives navigation and reload, and never reads as an order; its details say no order was created", async ({ page, api, capture }) => {
    fencing(api);
    await seed(page, READY);
    await seedGate(page, [UNKNOWN]);
    await page.goto(ORDERS_URL);
    await checkStatus(page).click();
    await expect(rows(page).filter({ hasText: FENCED })).toContainText("Your cart is unchanged.");
    await page.reload();
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText("Not placed");
    await expect(rows(page).first()).toContainText("Your cart is unchanged.");
    await page.getByRole("link", { name: FENCED, exact: true }).click();
    await expect(page).toHaveURL(`${STORE_URL}orders/${FENCED}`);
    await expect(page.getByRole("heading", { level: 1, name: "Order attempt" })).toBeVisible();
    await expect(page.getByText("Not placed", { exact: true })).toBeVisible();
    await expect(page.getByText("This order wasn’t placed and nothing was charged. Your cart is unchanged.")).toBeVisible();
    await expect(page.getByText("This reminder is kept in this browser only. No order was created.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Check status" })).toHaveCount(0);
    await settled(page);
    await capture("order-detail-not-placed");
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "Order attempt" })).toBeVisible();
  });

  test("Try again returns to checkout with the cart as it was; Place Order sends a new reference, once, and the old one is never reused", async ({ page, api }) => {
    const { refs } = fencing(api);
    await seed(page, READY);
    await seedGate(page, [UNKNOWN]);
    await page.goto(ORDERS_URL);
    await checkStatus(page).click();
    await tryAgain(page).click();
    await expect(page).toHaveURL(`${STORE_URL}checkout`);
    expect(refs).toEqual([]);
    expect(await storedCart(page)).toEqual(READY);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "Thank you." })).toBeVisible();
    expect(refs).toHaveLength(1);
    expect(refs[0]).toMatch(/^ATT-[0-9A-F]{4}-[0-9]{4}$/);
    expect(refs[0]).not.toBe(FENCED);
  });

  test("Dismiss removes the row for good; nothing else changes", async ({ page, api }) => {
    fencing(api);
    await seed(page, READY);
    await seedGate(page, [UNKNOWN]);
    await page.goto(ORDERS_URL);
    await checkStatus(page).click();
    await page.getByRole("button", { name: `Dismiss ${FENCED}` }).click();
    await expect(page.getByRole("heading", { level: 2, name: "No orders yet." })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("heading", { level: 2, name: "No orders yet." })).toBeVisible();
    expect(await storedCart(page)).toEqual(READY);
  });

  test("a check in another tab leaves the reminder here too, with one GET and one resolve in all (cross-tab, no duplicate)", async ({ page, api, context }) => {
    const { status } = fencing(api);
    await seed(page, READY);
    await seedGate(context, [UNKNOWN]);
    await page.goto(ORDERS_URL);
    const other = await context.newPage();
    await other.goto(ORDERS_URL);
    await rows(other).filter({ hasText: FENCED }).getByRole("button", { name: "Check status" }).click();
    for (const tab of [page, other]) await expect(rows(tab).filter({ hasText: FENCED })).toContainText("Your cart is unchanged.");
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(other)).toHaveCount(1);
    expect(status.seen).toEqual([`GET ${FENCED}`, `RESOLVE ${FENCED}`]);
    await other.close();
  });

  test("a failed check leaves the attempt unconfirmed and no reminder: nothing is claimed", async ({ page, api }) => {
    serve(api, () => ({ status: 500 }));
    serveStatus(api, { get: () => "network-error" });
    serveOrders(api, () => list([]));
    await seed(page, READY);
    await seedGate(page, [UNKNOWN]);
    await page.goto(ORDERS_URL);
    await checkStatus(page).click();
    await expect(page.getByText("Couldn’t check just now. Try again in a moment.")).toBeVisible();
    await expect(page.getByText("Your cart is unchanged.")).toHaveCount(0);
    await expect(rows(page).first()).toContainText("Status unknown");
    expect(await page.evaluate(() => localStorage.getItem("observa.closed"))).toBeNull();
  });
});
