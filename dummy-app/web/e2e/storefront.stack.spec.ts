// P4-I: the storefront against a real preview project (Gateway, Orders, Inventory, Payments,
// PostgreSQL), no mocks. Run only with E2E_STACK_URL=http://127.0.0.1:18000 (see support/stack.ts).
// The browser's own requests are real; Playwright only drops a checkout answer (route.fetch then
// abort: the Gateway placed the order, the browser never heard) or the whole request (abort: the
// Gateway never saw it), to produce an unknown outcome. Faults go through the preview controller.
import type { Page, Route } from "@playwright/test";
import { checkButton, fill, gateContents, place, placeButton, storedAttempts, storedCart } from "./support/checkout.ts";
import { STACK_ORIGIN, expect, test } from "./support/stack.ts";

/** Only the preview Gateway's checkout: a page route runs before the context guard. */
const CHECKOUT_API = `${STACK_ORIGIN}/api/checkout`;

const REF = /^ATT-[0-9A-F]{4}-[0-9]{4}$/;
const ORDER_NUMBER = /OBS-\d+-[0-9A-F]{4}/;
const thanks = (page: Page) => page.getByRole("heading", { level: 1, name: "Thank you." });
const unknownHeading = (page: Page) => page.getByRole("heading", { level: 1, name: "We couldn’t confirm your order." });
const notPlaced = (page: Page) => page.getByRole("heading", { level: 1, name: "Your order wasn’t placed." });
const declined = (page: Page) => page.getByRole("heading", { level: 1, name: "Payment didn’t go through." });
const collection = (page: Page) => page.getByRole("region", { name: "The Collection" });
const cartButton = (page: Page) => page.getByRole("banner").getByRole("button", { name: /^Cart,/ });
const cart = (page: Page) => page.getByRole("dialog", { name: "Cart" });
const ordersTable = (page: Page) => page.getByRole("table", { name: "Your orders" });
const rows = (page: Page) => ordersTable(page).getByRole("row").filter({ hasNot: page.getByRole("columnheader") });
/** The confirmed screen writes ?attempt= just after it renders, so wait for it. */
async function attemptFromUrl(page: Page) {
  await expect(page).toHaveURL(/[?&]attempt=ATT-/);
  return new URL(page.url()).searchParams.get("attempt") ?? "";
}

/** From the home page: Journal / Notebook once and Coffee Mug twice ($48.00), then to checkout. */
async function shopAndCheckout(page: Page) {
  await page.goto("/");
  await expect(collection(page).getByRole("listitem")).toHaveCount(5);
  await page.getByRole("button", { name: "Add to cart: Journal / Notebook" }).click();
  await page.getByRole("button", { name: "Add to cart: Coffee Mug" }).click();
  await page.getByRole("button", { name: "Add to cart: Coffee Mug" }).click();
  await expect(page.getByRole("button", { name: "Sold out: Limited Poster / Art Print" })).toBeDisabled();
  await cartButton(page).click();
  await expect(cart(page).getByText("$48.00").first()).toBeVisible();
  await cart(page).getByRole("link", { name: "Proceed to Checkout" }).click();
  await expect(page).toHaveURL(/\/checkout$/);
  await expect(page.getByText("$48.00").first()).toBeVisible();
}

const ORDERED = [
  { product_id: "sku-001", quantity: 1, unit_price_cents: 1200 },
  { product_id: "sku-002", quantity: 2, unit_price_cents: 1800 },
];

test.describe("real checkout", () => {
  test("from the collection to a confirmed order: the cart empties and My Orders lists it; a reload re-reads it (R4)", async ({ page, orders }) => {
    let posts = 0;
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/checkout") posts += 1;
    });
    await shopAndCheckout(page);
    await place(page);
    await expect(thanks(page)).toBeVisible();
    const ref = await attemptFromUrl(page);
    expect(ref).toMatch(REF);
    const number = (await page.getByText(ORDER_NUMBER).first().textContent())?.match(ORDER_NUMBER)?.[0];
    expect(number).toMatch(ORDER_NUMBER);
    expect(await storedCart(page)).toEqual([]);
    expect(await storedAttempts(page)).toEqual([]);

    // The real Gateway holds exactly this order for this browser's customer.
    const listed = await orders(page);
    expect(listed).toHaveLength(1);
    expect(listed[0]).toMatchObject({ attempt_ref: ref, order_number: number, outcome: "confirmed", total_cents: 4800, items: ORDERED });

    await page.reload();
    await expect(thanks(page)).toBeVisible();
    await expect(page.getByText(number ?? "missing")).toBeVisible();

    await page.getByRole("link", { name: "View My Orders" }).click();
    await expect(rows(page)).toHaveCount(1);
    await expect(rows(page).first()).toContainText(number ?? "missing");
    await expect(rows(page).first()).toContainText("Confirmed");
    await expect(rows(page).first()).toContainText("$48.00");
    await rows(page).first().getByRole("link").first().click();
    await expect(page).toHaveURL(new RegExp(`/orders/(${ref}|${number})$`));
    await expect(page.getByText(number ?? "missing").first()).toBeVisible();
    expect(posts).toBe(1);
  });

  test("search filters the real catalogue, and a found product goes into the cart", async ({ page }) => {
    await page.goto("/");
    await expect(collection(page).getByRole("listitem")).toHaveCount(5);
    await page.getByRole("banner").getByRole("button", { name: "Search" }).click();
    await page.getByRole("searchbox", { name: "Search products" }).fill("pen");
    await expect(collection(page).getByRole("heading", { level: 3 })).toHaveText(["Pen Set"]);
    await page.getByRole("button", { name: "Add to cart: Pen Set" }).click();
    await expect(cartButton(page)).toHaveAccessibleName(/^Cart, 1/);
    await cartButton(page).click();
    await expect(cart(page).getByText("$6.00").first()).toBeVisible();
  });
});

test.describe("unknown outcomes against the real Gateway", () => {
  test("the answer is lost after the Gateway placed the order: Check status finds it confirmed, one order only (U2)", async ({ page, orders }) => {
    const sent: string[] = [];
    const statuses: number[] = [];
    await page.route(CHECKOUT_API, async (route: Route) => {
      sent.push(route.request().headers()["idempotency-key"] ?? "");
      try {
        statuses.push((await route.fetch()).status()); // the real Gateway places the order...
      } finally {
        await route.abort("connectionreset"); // ...and the browser never hears back
      }
    });
    await shopAndCheckout(page);
    await place(page);
    await expect(unknownHeading(page)).toBeVisible();
    expect(sent).toHaveLength(1);
    expect(statuses).toEqual([201]);
    expect(await storedCart(page)).toHaveLength(2);

    await checkButton(page).click();
    await expect(thanks(page)).toBeVisible();
    await expect(page.getByText(ORDER_NUMBER).first()).toBeVisible();
    expect(await storedCart(page)).toEqual([]);
    expect((await gateContents(page)).attempts).toEqual([]);
    expect(sent).toHaveLength(1);
    const listed = await orders(page);
    expect(listed.map((order) => [order.attempt_ref, order.outcome])).toEqual([[sent[0], "confirmed"]]);
  });

  test("the request never reached the Gateway: a reload keeps the reference (R2), Check status resolves it as not placed, Try again is confirmed (U3)", async ({ page, orders }) => {
    const sent: string[] = [];
    await page.route(CHECKOUT_API, async (route: Route) => {
      sent.push(route.request().headers()["idempotency-key"] ?? "");
      if (sent.length === 1) return route.abort("connectionrefused");
      return route.fallback();
    });
    await shopAndCheckout(page);
    await place(page);
    await expect(unknownHeading(page)).toBeVisible();
    await expect(page.getByText(sent[0] ?? "missing").first()).toBeVisible();
    await page.reload();
    await expect(unknownHeading(page)).toBeVisible();
    await expect(page.getByText(sent[0] ?? "missing").first()).toBeVisible();

    await checkButton(page).click();
    await expect(notPlaced(page)).toBeVisible();
    expect(await storedCart(page)).toHaveLength(2);
    await page.getByRole("button", { name: "Try again" }).click();
    await place(page);
    await expect(thanks(page)).toBeVisible();
    expect(sent).toHaveLength(2);
    expect(sent[1]).toMatch(REF);
    expect(sent[1]).not.toBe(sent[0]);
    // The fenced reference is not an order; only the retry is.
    const listed = await orders(page);
    expect(listed.map((order) => [order.attempt_ref, order.outcome])).toEqual([[sent[1], "confirmed"]]);
  });

  // The real Gateway never lists a fenced attempt (GET /api/orders leaves abandoned ones out), so
  // once resolved it leaves My Orders in both tabs. The mocked T4 test lists it as Not placed.
  test("an unknown attempt shows in another tab's My Orders; Check status there fences it and both tabs drop it (T4)", async ({ page, context }) => {
    const sent: string[] = [];
    await page.route(CHECKOUT_API, (route) => {
      sent.push(route.request().headers()["idempotency-key"] ?? "");
      return route.abort("connectionrefused");
    });
    await shopAndCheckout(page);
    await place(page);
    await expect(unknownHeading(page)).toBeVisible();
    const ref = sent[0] ?? "missing";
    await page.goto("/orders");
    const other = await context.newPage();
    await other.goto("/orders");
    for (const tab of [page, other]) await expect(rows(tab).filter({ hasText: ref })).toContainText("Status unknown");
    const fenced = other.waitForResponse((response) => new URL(response.url()).pathname === `/api/attempts/${ref}/resolve`);
    await rows(other).filter({ hasText: ref }).getByRole("button", { name: "Check status" }).click();
    const answer = await fenced;
    expect(answer.status()).toBe(409);
    expect(await answer.json()).toMatchObject({ error_code: "attempt_abandoned", details: { attempt_ref: ref, outcome: "abandoned" } });
    for (const tab of [page, other]) {
      await expect(tab.getByText("No orders yet.")).toBeVisible();
      await expect(rows(tab).filter({ hasText: ref })).toHaveCount(0);
    }
    expect(await storedAttempts(page)).toEqual([]);
    expect(await storedCart(page)).toHaveLength(2);
    await other.close();
  });
});

test.describe("faults through the preview controller", () => {
  test("F2 declines the payment; once the fault is stopped, Try again is confirmed with a new reference", async ({ page, faults, orders }) => {
    const sent: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST" && new URL(request.url()).pathname === "/api/checkout") sent.push(request.headers()["idempotency-key"] ?? "");
    });
    await shopAndCheckout(page);
    await fill(page);
    await faults.activate("payments_errors", { failure_rate: 1, status_code: 402 });
    await placeButton(page).click();
    await expect(declined(page)).toBeVisible();
    const first = sent[0];
    expect(sent).toHaveLength(1);
    expect(await storedCart(page)).toHaveLength(2);
    await faults.deactivate("payments_errors");

    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByLabel("Email")).toHaveValue("demo@example.com");
    await placeButton(page).click();
    await expect(thanks(page)).toBeVisible();
    const second = await attemptFromUrl(page);
    expect(sent).toEqual([first, second]);
    expect(second).toMatch(REF);
    expect(second).not.toBe(first);
    const listed = await orders(page);
    expect(listed.map((order) => [order.attempt_ref, order.outcome])).toEqual([
      [second, "confirmed"],
      [first, "payment_failed"],
    ]);

    await page.getByRole("link", { name: "View My Orders" }).click();
    await expect(rows(page)).toHaveCount(2);
    await expect(rows(page).nth(0)).toContainText("Confirmed");
    await expect(rows(page).nth(1)).toContainText("Payment declined");
  });
});

test.describe("stack guard", () => {
  test("a browser request off the preview origin, such as the live Gateway, is blocked and recorded", async ({ page, offOrigin }) => {
    await page.goto("/");
    const outcome = await page.evaluate(() =>
      fetch("http://127.0.0.1:8000/health").then(
        () => "sent",
        () => "blocked",
      ),
    );
    expect(outcome).toBe("blocked");
    expect(offOrigin).toEqual(["http://127.0.0.1:8000/health"]);
    offOrigin.length = 0; // taken by this test, so the teardown check passes
  });
});
