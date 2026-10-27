// P4-E: unknown checkout recovery, against the production build with typed mocks.
// Every checkout POST and every Check status request (GET, resolve) is counted. Polling runs on
// Playwright's clock, so the 1, 2, 4, 8 s backoff is stepped through exactly.
// Safety cases from e2e/README.md: R2, R3, R4, T5, T5b, U2, U3, U3b, U4, U5, plus 422 handling,
// privacy and a reload right after a confirmation.
import type { Page } from "@playwright/test";
import {
  CHECKOUT_URL,
  CUSTOMER,
  checkButton,
  envelope,
  fenced,
  gateContents,
  gateRecord,
  place,
  placeButton,
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

const REF = "ATT-1A2B-0042";
const notFound: MockReply = { status: 404, body: { error_code: "attempt_not_found", message: "No checkout recorded for this reference yet" } };
const unknownHeading = (page: Page) => page.getByRole("heading", { level: 1, name: "We couldn’t confirm your order." });
const thanks = (page: Page) => page.getByRole("heading", { level: 1, name: "Thank you." });

test.describe("Check status", () => {
  test("the response was lost after the server placed it: Check status shows it confirmed, no duplicate (U2)", async ({ page, api }) => {
    // The mock records the order, then drops the answer, as a broken connection would.
    const placed = new Map<string, MockReply>();
    const refs = serve(api, (ref) => {
      placed.set(ref, { status: 200, body: result(ref, "confirmed") });
      return "network-error";
    });
    const status = serveStatus(api, { get: (ref) => placed.get(ref) ?? notFound });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(unknownHeading(page)).toBeVisible();
    await checkButton(page).click();
    await expect(thanks(page)).toBeVisible();
    await expect(page.getByText("OBS-1042-7F3A")).toBeVisible();
    expect(await storedCart(page)).toEqual([]);
    expect(await storedAttempts(page)).toEqual([]);
    expect(refs).toHaveLength(1);
    expect(status.seen).toEqual([`GET ${refs[0]}`]);
    expect(status.queries).toEqual([`customer_id=${CUSTOMER}`]);
    await expect(page).toHaveURL(`${CHECKOUT_URL}?attempt=${refs[0]}`);
  });

  test("404 → resolve → not placed, safe to retry; Try again uses a new reference (U3)", async ({ page, api, capture }) => {
    const refs = serve(api, (ref) => (refs.length === 1 ? "network-error" : { status: 201, body: result(ref, "confirmed") }));
    const status = serveStatus(api, { get: () => notFound, resolve: (ref) => ({ status: 409, body: envelope("attempt_abandoned", fenced(ref)) }) });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await checkButton(page).click();
    await expect(page.getByRole("heading", { level: 1, name: "Your order wasn’t placed." })).toBeVisible();
    expect(status.seen).toEqual([`GET ${refs[0]}`, `RESOLVE ${refs[0]}`]);
    expect(await storedCart(page)).toEqual(READY);
    await settled(page);
    await capture("outcome-abandoned");
    await page.getByRole("button", { name: "Try again" }).click();
    await place(page);
    await expect(thanks(page)).toBeVisible();
    expect(refs).toHaveLength(2);
    expect(refs[1]).not.toBe(refs[0]);
    expect(refs[1]).toMatch(/^ATT-[0-9A-F]{4}-[0-9]{4}$/);
  });

  test("404 → resolve may find it recorded meanwhile, declined (U3b)", async ({ page, api }) => {
    await seed(page, READY);
    await seedGate(page, [gateRecord(REF, "unknown")]);
    serve(api, () => ({ status: 500 }));
    serveStatus(api, { get: () => notFound, resolve: (ref) => ({ status: 402, body: envelope("payment_declined", result(ref, "payment_failed", { error_code: "payment_declined" })) }) });
    await page.goto(CHECKOUT_URL);
    await checkButton(page).click();
    await expect(page.getByRole("heading", { level: 1, name: "Payment didn’t go through." })).toBeVisible();
    expect(await storedCart(page)).toEqual(READY);
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
  });

  test("a failed check says so, stays paused and never resolves (U5)", async ({ page, api }) => {
    await seed(page, READY);
    await seedGate(page, [gateRecord(REF, "unknown")]);
    const refs = serve(api, () => ({ status: 500 }));
    const status = serveStatus(api, { get: () => "network-error", resolve: () => ({ status: 500 }) });
    await page.goto(CHECKOUT_URL);
    await checkButton(page).click();
    await expect(page.getByRole("status").filter({ hasText: "We couldn’t check its status just now." })).toBeVisible();
    await expect(unknownHeading(page)).toBeVisible();
    await expect(placeButton(page)).toHaveCount(0);
    expect(status.seen).toEqual([`GET ${REF}`]);
    expect(refs).toEqual([]);
  });

  test("422 from the Gateway on checkout: unknown, and Check status heals it through resolve", async ({ page, api }) => {
    const refs = serve(api, () => ({ status: 422, body: { error_code: "validation_error", message: "Request did not match the API schema" } }));
    const status = serveStatus(api, { get: () => notFound, resolve: (ref) => ({ status: 409, body: envelope("attempt_abandoned", fenced(ref)) }) });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(unknownHeading(page)).toBeVisible();
    await checkButton(page).click();
    await expect(page.getByRole("heading", { level: 1, name: "Your order wasn’t placed." })).toBeVisible();
    expect(status.seen).toEqual([`GET ${refs[0]}`, `RESOLVE ${refs[0]}`]);
  });

  test("422 on Check status itself stays unknown and never resolves", async ({ page, api }) => {
    await seed(page, READY);
    await seedGate(page, [gateRecord(REF, "unknown")]);
    serve(api, () => ({ status: 500 }));
    const status = serveStatus(api, { get: () => ({ status: 422, body: { error_code: "invalid_attempt_key", message: "x" } }) });
    await page.goto(CHECKOUT_URL);
    await checkButton(page).click();
    await expect(page.getByText("We couldn’t check its status just now.", { exact: false })).toBeVisible();
    expect(status.seen).toEqual([`GET ${REF}`]);
  });
});

test.describe("processing (U4, R3)", () => {
  const STEPS = [1000, 2000, 4000, 8000, 8000, 8000, 8000, 8000, 8000, 8000];

  test("202 → GET polling with backoff → confirmed; never a second POST", async ({ page, api }) => {
    await page.clock.install();
    let polls = 0;
    const refs = serve(api, (ref) => ({ status: 202, body: result(ref, "processing") }));
    const status = serveStatus(api, { get: (ref) => (++polls < 3 ? { status: 200, body: result(ref, "processing") } : { status: 200, body: result(ref, "confirmed") }) });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByRole("heading", { level: 1, name: "Your order is still processing." })).toBeVisible();
    await expect(page.getByText("Checking for an update automatically…")).toBeVisible();
    for (const [i, step] of STEPS.slice(0, 3).entries()) {
      await page.clock.runFor(step - 1);
      expect(status.count("GET")).toBe(i);
      await page.clock.runFor(1);
      await expect.poll(() => status.count("GET")).toBe(i + 1);
    }
    await expect(thanks(page)).toBeVisible();
    expect(refs).toHaveLength(1);
    expect(status.count("RESOLVE")).toBe(0);
    expect(await storedCart(page)).toEqual([]);
  });

  test("still processing after about 60 s: Check again, which GETs first and then resolves once", async ({ page, api, capture }) => {
    await page.clock.install();
    let resolving = false;
    const refs = serve(api, (ref) => ({ status: 202, body: result(ref, "processing") }));
    const status = serveStatus(api, {
      get: (ref) => ({ status: 200, body: result(ref, "processing") }),
      resolve: (ref) => {
        resolving = true;
        return { status: 201, body: result(ref, "confirmed") };
      },
    });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(page.getByText("Checking for an update automatically…")).toBeVisible();
    for (const [i, step] of STEPS.entries()) {
      await page.clock.runFor(step);
      await expect.poll(() => status.count("GET")).toBe(i + 1);
    }
    await expect(page.getByRole("button", { name: "Check again" })).toBeVisible();
    await expect(page.getByText("This is taking longer than usual.", { exact: false })).toBeVisible();
    // Polling has stopped: more time passes, nothing more is asked.
    await page.clock.runFor(30_000);
    expect(status.count("GET")).toBe(STEPS.length);
    expect(resolving).toBe(false);
    await settled(page);
    await capture("outcome-still-processing");
    await page.getByRole("button", { name: "Check again" }).click();
    await expect(thanks(page)).toBeVisible();
    expect(status.seen.slice(-2)).toEqual([`GET ${refs[0]}`, `RESOLVE ${refs[0]}`]);
    expect(refs).toHaveLength(1);
  });

  test("a reload while processing resumes polling with GET, never a POST (R3)", async ({ page, api }) => {
    await page.clock.install();
    const refs = serve(api, () => ({ status: 500 }));
    const status = serveStatus(api, { get: (ref) => ({ status: 200, body: result(ref, "payment_failed", { error_code: "payment_declined" }) }) });
    await seed(page, READY);
    await seedGate(page, [gateRecord(REF, "processing")]);
    await page.goto(CHECKOUT_URL);
    await expect(page.getByRole("heading", { level: 1, name: "Your order is still processing." })).toBeVisible();
    await page.clock.runFor(1000);
    await expect(page.getByRole("heading", { level: 1, name: "Payment didn’t go through." })).toBeVisible();
    expect(status.seen).toEqual([`GET ${REF}`]);
    expect(refs).toEqual([]);
  });

  test("leaving the processing screen stops polling", async ({ page, api }) => {
    await page.clock.install();
    serve(api, () => ({ status: 500 }));
    const status = serveStatus(api, { get: (ref) => ({ status: 200, body: result(ref, "processing") }) });
    await seed(page, READY);
    await seedGate(page, [gateRecord(REF, "processing")]);
    await page.goto(CHECKOUT_URL);
    await expect(page.getByText("Checking for an update automatically…")).toBeVisible();
    await page.getByRole("main").getByRole("link", { name: "Return to cart" }).click();
    await expect(page.getByRole("heading", { level: 1, name: /Quiet objects/ })).toBeVisible();
    await page.clock.runFor(70_000);
    expect(status.count("GET")).toBe(0);
  });
});

test.describe("reloads (R2, R4)", () => {
  test("a reload on the unknown screen keeps the reference; Check status reuses it (R2)", async ({ page, api, capture }) => {
    const refs = serve(api, () => "network-error");
    const status = serveStatus(api, { get: (ref) => ({ status: 200, body: result(ref, "confirmed") }) });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(unknownHeading(page)).toBeVisible();
    await page.reload();
    await expect(unknownHeading(page)).toBeVisible();
    await expect(page.getByText(refs[0] ?? "missing")).toBeVisible();
    await expect(checkButton(page)).toBeVisible();
    await settled(page);
    await capture("outcome-unknown");
    await checkButton(page).click();
    await expect(thanks(page)).toBeVisible();
    expect(status.seen).toEqual([`GET ${refs[0]}`]);
    expect(refs).toHaveLength(1);
  });

  test("a reload on the confirmation re-reads the order number; the cart stays cleared (R4)", async ({ page, api }) => {
    const refs = serve(api, (ref) => ({ status: 201, body: result(ref, "confirmed") }));
    const status = serveStatus(api, { get: (ref) => ({ status: 200, body: result(ref, "confirmed") }) });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect(thanks(page)).toBeVisible();
    await expect(page).toHaveURL(`${CHECKOUT_URL}?attempt=${refs[0]}`);
    // Straight away: the confirmed attempt was settled before the screen showed.
    await page.reload();
    await expect(thanks(page)).toBeVisible();
    await expect(page.getByText("OBS-1042-7F3A")).toBeVisible();
    await expect(checkButton(page)).toHaveCount(0);
    expect(status.seen).toEqual([`GET ${refs[0]}`]);
    expect(refs).toHaveLength(1);
    expect(await storedCart(page)).toEqual([]);
    expect((await gateContents(page)).attempts).toEqual([]);
  });
});

test.describe("other tabs (T5, T5b)", () => {
  test("both tabs check the same attempt: both confirmed, the cart loses the ordered units once (T5)", async ({ page, api, context }) => {
    serve(api, () => ({ status: 500 }));
    // Both checks are in flight together: the first GET is held until the second arrives.
    const waiting: (() => void)[] = [];
    const status = serveStatus(api, {
      get: (ref) =>
        new Promise<MockReply>((resolve) => {
          waiting.push(() => resolve({ status: 200, body: result(ref, "confirmed") }));
          if (waiting.length === 2) waiting.forEach((answer) => answer());
        }),
    });
    // The cart holds one more sku-001 than the attempt ordered (added while it was unknown).
    await seed(page, [{ product_id: "sku-001", quantity: 2 }, ...READY.slice(1)]);
    await seedGate(context, [gateRecord(REF, "unknown")]);
    await page.goto(CHECKOUT_URL);
    const other = await context.newPage();
    await other.goto(CHECKOUT_URL);
    for (const tab of [page, other]) await expect(checkButton(tab)).toBeVisible();
    await Promise.all([checkButton(page).click(), checkButton(other).click()]);
    for (const tab of [page, other]) await expect(thanks(tab)).toBeVisible();
    expect(await storedCart(page)).toEqual([{ product_id: "sku-001", quantity: 1 }]);
    expect(status.count("RESOLVE")).toBe(0);
    await other.close();
  });

  test("a stale tab's answer for a reference the other tab fenced is unknown, then not placed (T5b)", async ({ page, api, context }) => {
    let release: (reply: MockReply) => void = () => undefined;
    const refs = serve(api, () => new Promise<MockReply>((resolve) => (release = resolve)));
    const status = serveStatus(api, {
      get: (ref) => (status.count("RESOLVE") > 0 ? { status: 200, body: fenced(ref) } : notFound),
      resolve: (ref) => ({ status: 409, body: envelope("attempt_abandoned", fenced(ref)) }),
    });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await expect.poll(() => refs.length).toBe(1);
    // The second tab finds the attempt still unanswered and checks it: nothing recorded, so fenced.
    const other = await context.newPage();
    await other.goto(CHECKOUT_URL);
    await checkButton(other).click();
    await expect(other.getByRole("heading", { level: 1, name: "Your order wasn’t placed." })).toBeVisible();
    // The first tab's POST now reaches a fenced reference: 409 without a result.
    release({ status: 409, body: envelope("attempt_abandoned") });
    await expect(unknownHeading(page)).toBeVisible();
    await checkButton(page).click();
    await expect(page.getByRole("heading", { level: 1, name: "Your order wasn’t placed." })).toBeVisible();
    expect(refs).toHaveLength(1);
    expect(await storedCart(page)).toEqual(READY);
    await other.close();
  });

  test("the cart's pause message leads to the attempt to check it", async ({ page, api, capture }) => {
    serve(api, () => ({ status: 500 }));
    serveStatus(api, {});
    await seed(page, READY);
    await seedGate(page, [gateRecord(REF, "unknown")]);
    await page.goto(STORE_URL);
    await page.getByRole("banner").getByRole("button", { name: /^Cart,/ }).click();
    const cart = page.getByRole("dialog", { name: "Cart" });
    await expect(cart.getByText(new RegExp(REF))).toBeVisible();
    await expect(cart.getByRole("button", { name: "Proceed to Checkout" })).toBeDisabled();
    await settled(page);
    await capture("checkout-unconfirmed-prompt", "viewport");
    await cart.getByRole("link", { name: "Check its status" }).click();
    await expect(page).toHaveURL(CHECKOUT_URL);
    await expect(checkButton(page)).toBeVisible();
  });
});

test.describe("privacy", () => {
  test("after an unknown answer and recovery, no contact detail is in storage, the gate, the address or the status requests", async ({ page, api }) => {
    serve(api, () => "network-error");
    serveStatus(api, { get: () => notFound, resolve: (ref) => ({ status: 409, body: envelope("attempt_abandoned", fenced(ref)) }) });
    await seed(page, READY);
    await page.goto(CHECKOUT_URL);
    await place(page);
    await checkButton(page).click();
    await expect(page.getByRole("heading", { level: 1, name: "Your order wasn’t placed." })).toBeVisible();
    const held = await page.evaluate(() => ({
      local: { ...localStorage },
      session: { ...sessionStorage },
      url: location.href,
      history: JSON.stringify(history.state),
    }));
    const recovery = api.calls.filter((c) => c.path.startsWith("/api/attempts/")).map((c) => `${c.path}?${c.query.toString()} ${JSON.stringify(c.body ?? null)}`);
    const everything = JSON.stringify({ held, gate: await gateContents(page), recovery });
    for (const secret of ["Demo Name", "demo@example.com", "example.com"]) expect(everything).not.toContain(secret);
  });
});
