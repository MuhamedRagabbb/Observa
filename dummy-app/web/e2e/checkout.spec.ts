// P4-D: checkout and definite outcomes, against the production build with typed mocks.
// Every POST /api/checkout is counted: no test may see more POSTs than presses.
// Safety cases from e2e/README.md: E1-E3 (via the outcome screens), R1, R5, T3, T6, U1, U6, U7,
// plus two tabs pressing Place Order together. Recovery (P4-E) is in recovery.spec.ts.
import type { BrowserContext, Page } from "@playwright/test";
import {
  CHECKOUT_URL,
  envelope,
  fill,
  gateRecord,
  isDesktop,
  place,
  placeButton,
  pressTogether,
  READY,
  result,
  seed,
  seedGate,
  serve,
  serveStatus,
  settled,
  storedAttempts,
  storedCart,
} from "./support/checkout.ts";
import { STORE_URL, expect, test, type MockReply } from "./support/harness.ts";

test.describe("checkout form", () => {
  test("form with the server-priced summary", async ({ page, api, capture }, info) => {
    serve(api, () => ({ status: 500 }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await expect(page.getByRole("heading", { level: 1, name: "Checkout" })).toBeFocused();
    await expect(placeButton(page)).toHaveText(/Place Order · \$54\.00/);
    await expect(page.getByRole("link", { name: "Return to cart" })).toBeVisible();
    if (isDesktop(info.project.name)) {
      await expect(page.getByRole("region", { name: "Order summary" })).toContainText("Qty 2 · $18.00 each");
    } else {
      await expect(page.getByText("Show order summary")).toBeVisible();
    }
    await settled(page);
    await capture("checkout-form");
  });

  test("invalid contact: errors in place, focus on the first, nothing sent", async ({ page, api, capture }) => {
    const refs = serve(api, () => ({ status: 500 }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await page.getByLabel("Email").fill("demo@example");
    await expect(placeButton(page)).not.toHaveAttribute("aria-disabled", "true");
    await placeButton(page).click();
    await expect(page.getByLabel("Full name")).toBeFocused();
    await expect(page.getByLabel("Full name")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByText("Enter an email like demo@example.com.")).toBeVisible();
    await settled(page);
    await capture("checkout-form-invalid");
    expect(refs).toEqual([]);
  });

  test("placing: busy button and status; a double-click sends one POST (U7)", async ({ page, api, capture }) => {
    const refs = serve(api, () => new Promise<MockReply>(() => undefined));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await fill(page);
    await expect(placeButton(page)).not.toHaveAttribute("aria-disabled", "true");
    await placeButton(page).dblclick();
    await expect(page.getByText("Submitting your order…")).toBeVisible();
    await expect(page.getByRole("button", { name: /Placing order/ })).toHaveAttribute("aria-busy", "true");
    await page.waitForTimeout(300);
    expect(refs).toHaveLength(1);
    await settled(page);
    await capture("outcome-processing");
  });
});

test.describe("definite outcomes", () => {
  test("confirmed: order number, cart cleared, record settled", async ({ page, api, capture }) => {
    const refs = serve(api, (ref) => ({ status: 201, body: result(ref, "confirmed") }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "Thank you." })).toBeFocused();
    // Place Order sits low on phones: the new screen starts at the top, title in view.
    await expect(page.getByRole("heading", { level: 1, name: "Thank you." })).toBeInViewport();
    await expect(page.getByText("OBS-1042-7F3A")).toBeVisible();
    expect(await storedCart(page)).toEqual([]);
    expect(await storedAttempts(page)).toEqual([]);
    expect(refs).toHaveLength(1);
    await settled(page);
    await capture("outcome-success");
  });

  test("payment declined, then Try again with a new reference", async ({ page, api, capture }) => {
    const refs = serve(api, (ref) => ({ status: 402, body: envelope("payment_declined", result(ref, "payment_failed", { error_code: "payment_declined" })) }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "Payment didn’t go through." })).toBeVisible();
    expect(await storedCart(page)).toEqual(READY);
    await settled(page);
    await capture("outcome-payment-declined");
    // Try again returns to the form with the details kept; Place Order sends a new attempt.
    await page.getByRole("button", { name: "Try again" }).click();
    await expect(page.getByLabel("Email")).toHaveValue("demo@example.com");
    await expect(placeButton(page)).not.toHaveAttribute("aria-disabled", "true");
    await placeButton(page).click();
    await expect.poll(() => refs.length).toBe(2);
    expect(refs[1]).not.toBe(refs[0]);
  });

  test("payment unavailable", async ({ page, api, capture }) => {
    serve(api, (ref) => ({ status: 503, body: envelope("payment_unavailable", result(ref, "payment_failed", { error_code: "payment_unavailable" })) }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "Payment couldn’t be completed." })).toBeVisible();
    await settled(page);
    await capture("outcome-payment-unavailable");
  });

  test("not placed: the unavailable product named and marked; Review cart opens the cart", async ({ page, api, capture }) => {
    serve(api, (ref) => ({
      status: 409,
      body: envelope("insufficient_stock", result(ref, "unavailable", { error_code: "insufficient_stock", unavailable_items: [{ product_id: "sku-002" }] })),
    }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "We couldn’t place your order." })).toBeVisible();
    await expect(page.locator("strong", { hasText: "Coffee Mug" })).toBeVisible();
    await settled(page);
    await capture("outcome-not-placed");
    await page.getByRole("link", { name: "Review cart" }).click();
    await expect(page.getByRole("dialog", { name: "Cart" })).toBeVisible();
  });

  test("failed, not retryable", async ({ page, api, capture }) => {
    serve(api, (ref) => ({ status: 500, body: envelope("order_persistence_error", result(ref, "failed", { retryable: false, error_code: "order_persistence_error" })) }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "Something went wrong." })).toBeVisible();
    await expect(page.getByRole("button", { name: "Try again" })).toHaveCount(0);
    await settled(page);
    await capture("outcome-failed");
  });

  test("a result for another reference is not trusted (E3)", async ({ page, api }) => {
    serve(api, () => ({ status: 201, body: result("ATT-FFFF-9999", "confirmed") }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "We couldn’t confirm your order." })).toBeVisible();
    expect(await storedCart(page)).toEqual(READY);
  });
});

test.describe("unknown outcomes are kept", () => {
  test("network error: unknown with the reference, record kept, nothing resent (U1)", async ({ page, api, capture }) => {
    const refs = serve(api, () => "network-error");
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "We couldn’t confirm your order." })).toBeVisible();
    await expect(page.getByText(refs[0] ?? "missing")).toBeVisible();
    expect(await storedAttempts(page)).toEqual([expect.objectContaining({ attempt_ref: refs[0], status: "unknown" })]);
    expect(await storedCart(page)).toEqual(READY);
    await settled(page);
    await capture("outcome-unknown");
    await page.waitForTimeout(300);
    expect(refs).toHaveLength(1);
  });

  test("409 idempotency_conflict without a result is unknown, not a failure (U6)", async ({ page, api }) => {
    serve(api, () => ({ status: 409, body: envelope("idempotency_conflict") }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "We couldn’t confirm your order." })).toBeVisible();
    expect((await storedAttempts(page)).map((a) => a.status)).toEqual(["unknown"]);
  });

  test("reload while the POST is in flight: the same reference, unknown, no second POST (R1)", async ({ page, api }) => {
    const refs = serve(api, () => new Promise<MockReply>(() => undefined));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect.poll(() => refs.length).toBe(1);
    await page.reload();
    // The answer died with the page: the attempt is still "submitting", shown as status unknown.
    await expect(page.getByText("Status unknown")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1, name: "We’re still waiting to confirm your order." })).toBeVisible();
    await expect(page.getByText(refs[0] ?? "missing")).toBeVisible();
    await expect(page.getByRole("button", { name: "Check Order Status" })).toBeVisible();
    await expect(page.getByLabel("Full name")).toHaveCount(0);
    await page.waitForTimeout(300);
    expect(refs).toHaveLength(1);
  });

  test("the gate still holds an unknown attempt after its localStorage copy is wiped", async ({ page, api }) => {
    const refs = serve(api, () => "network-error");
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "We couldn’t confirm your order." })).toBeVisible();
    await page.evaluate(() => localStorage.removeItem("observa.attempts"));
    await page.reload();
    // The IndexedDB gate restores the record: still paused, same reference, nothing new sent.
    await expect(page.getByText(refs[0] ?? "missing")).toBeVisible();
    await expect(placeButton(page)).toHaveCount(0);
    expect(refs).toHaveLength(1);
  });

  test("an unconfirmed attempt pauses new checkouts in the cart and on the page (T6)", async ({ page, api }) => {
    const refs = serve(api, () => ({ status: 500 }));
    await seed(page, READY);
    await seedGate(page, [gateRecord("ATT-1A2B-0042", "unknown")]);
    await page.goto(STORE_URL);
    await page.getByRole("banner").getByRole("button", { name: /^Cart,/ }).click();
    const cart = page.getByRole("dialog", { name: "Cart" });
    await expect(cart.getByRole("button", { name: "Proceed to Checkout" })).toBeDisabled();
    await expect(cart.getByText(/ATT-1A2B-0042/)).toBeVisible();
    await page.goto(CHECKOUT_URL);
    await expect(page.getByText("ATT-1A2B-0042")).toBeVisible();
    await expect(placeButton(page)).toHaveCount(0);
    expect(refs).toEqual([]);
  });
});

test.describe("navigation and other tabs", () => {
  test("Back and Forward after success never resubmit; Forward re-reads the confirmation (R5, R4)", async ({ page, api }) => {
    const refs = serve(api, (ref) => ({ status: 201, body: result(ref, "confirmed") }));
    const status = serveStatus(api, { get: (ref) => ({ status: 200, body: result(ref, "confirmed") }) });
    await seed(page, READY);
    await page.goto(STORE_URL);
    await page.getByRole("banner").getByRole("button", { name: /^Cart,/ }).click();
    await page.getByRole("link", { name: "Proceed to Checkout" }).click();
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "Thank you." })).toBeVisible();
    await page.goBack();
    await expect(page.getByRole("heading", { level: 1, name: /Quiet objects/ })).toBeVisible();
    await page.goForward();
    // The confirmation is shown again from the server (R4): re-read by reference, never re-sent.
    await expect(page.getByRole("heading", { level: 1, name: "Thank you." })).toBeVisible();
    await expect(placeButton(page)).toHaveCount(0);
    expect(status.seen).toEqual([`GET ${refs[0]}`]);
    expect(refs).toHaveLength(1);
  });

  async function twoTabs(context: BrowserContext) {
    const other = await context.newPage();
    await other.goto(CHECKOUT_URL);
    return other;
  }

  /** Both tabs ready to press Place Order, the POST never answering (so nothing settles). */
  async function readyTabs(page: Page, context: BrowserContext) {
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    const other = await twoTabs(context);
    for (const tab of [page, other]) {
      await fill(tab);
      await expect(placeButton(tab)).not.toHaveAttribute("aria-disabled", "true");
    }
    return other;
  }

  test("two tabs pressing Place Order at the same instant send one POST; the other tab shows that attempt", async ({ page, api, context }) => {
    const refs = serve(api, () => new Promise<MockReply>(() => undefined));
    const other = await readyTabs(page, context);
    // Both presses fire at one shared wall-clock instant, inside the pages.
    await pressTogether([page, other]);
    await expect.poll(() => refs.length).toBe(1);
    await page.waitForTimeout(500);
    expect(refs).toHaveLength(1);
    for (const tab of [page, other]) await expect(tab.getByText(refs[0] ?? "missing").or(tab.getByText("Submitting your order…"))).toBeVisible();
    await other.close();
  });

  test.describe("with the gate's check deliberately removed (test-only mutant)", () => {
    // The page's IndexedDB is patched in the browser, never in the shipped code: inside a
    // read-write transaction the attempts store reads as empty. The same race then sends exactly
    // two POSTs, so the "one POST" assertion above can tell a broken gate from a working one.
    test("the same race sends two POSTs, so the race test above can fail", async ({ page, api, context }) => {
      await context.addInitScript(() => {
        const getAll = IDBObjectStore.prototype.getAll;
        IDBObjectStore.prototype.getAll = function (this: IDBObjectStore, ...args: Parameters<IDBObjectStore["getAll"]>) {
          if (this.name === "attempts" && this.transaction.mode === "readwrite") return getAll.call(this, IDBKeyRange.only("~none~"));
          return getAll.apply(this, args);
        };
      });
      const refs = serve(api, () => new Promise<MockReply>(() => undefined));
      const other = await readyTabs(page, context);
      await pressTogether([page, other]);
      await expect.poll(() => refs.length).toBe(2);
      await page.waitForTimeout(500);
      expect(refs).toHaveLength(2);
      expect(new Set(refs).size).toBe(2);
      await other.close();
    });
  });

  test("without IndexedDB nothing is sent: checkout fails closed", async ({ page, api, context }) => {
    await context.addInitScript(() => Object.defineProperty(window, "indexedDB", { value: undefined, configurable: true }));
    const refs = serve(api, (ref) => ({ status: 201, body: result(ref, "confirmed") }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByText(/didn’t let this page save the order’s reference, so nothing was sent/)).toBeVisible();
    await page.waitForTimeout(300);
    expect(refs).toEqual([]);
    expect(await storedCart(page)).toEqual(READY);
  });

  test("success in one tab empties the cart in the other, which cannot submit it (T3)", async ({ page, api, context }) => {
    const refs = serve(api, (ref) => ({ status: 201, body: result(ref, "confirmed") }));
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    const other = await twoTabs(context);
    await expect(placeButton(other)).toBeVisible();
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "Thank you." })).toBeVisible();
    await expect(other.getByText("Your cart is empty.")).toBeVisible();
    await expect(placeButton(other)).toHaveCount(0);
    expect(refs).toHaveLength(1);
    await other.close();
  });
});

test.describe("cart checks", () => {
  test("counts: the header counts every unit, the cart counts units that can be bought; the subtotal leaves unavailable lines out", async ({ page, api }) => {
    serve(api, () => ({ status: 500 }));
    await seed(page, [...READY, { product_id: "sku-004", quantity: 2 }]);
    await page.goto(STORE_URL);
    const cartButton = page.getByRole("banner").getByRole("button", { name: /^Cart,/ });
    await expect(cartButton).toHaveAccessibleName("Cart, 6 items");
    await cartButton.click();
    const cart = page.getByRole("dialog", { name: "Cart" });
    await expect(cart.getByText("4 items · 1 unavailable")).toBeVisible();
    await expect(cart.getByText("$54.00")).toBeVisible();
    await expect(cart.getByRole("button", { name: "Proceed to Checkout" })).toBeDisabled();
  });

  test("the last line can be scrolled fully above the cart's footer", async ({ page, api }) => {
    serve(api, () => ({ status: 500 }));
    await seed(page, [...READY, { product_id: "sku-004", quantity: 1 }, { product_id: "sku-005", quantity: 3 }]);
    await page.goto(STORE_URL);
    await page.getByRole("banner").getByRole("button", { name: /^Cart,/ }).click();
    const cart = page.getByRole("dialog", { name: "Cart" });
    await expect(cart.getByRole("heading", { name: "Organizing Labels" })).toBeAttached();
    const last = cart.locator("li").last();
    await last.scrollIntoViewIfNeeded();
    const footer = cart.getByText("Demo checkout. No real payment or shipping.").locator("..");
    const [lastBox, footerBox] = [await last.boundingBox(), await footer.boundingBox()];
    if (!lastBox || !footerBox) throw new Error("not laid out");
    expect(lastBox.y + lastBox.height).toBeLessThanOrEqual(footerBox.y + 1);
    await expect(cart.getByRole("button", { name: "Remove Organizing Labels" })).toBeInViewport();
  });
});
