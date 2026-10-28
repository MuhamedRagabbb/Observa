// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/"}
// My Orders (P4-F): GET /api/orders?customer_id=&limit=50 merged with this browser's
// unconfirmed attempts; order details at /orders/:ref; Check status through the P4-E flow.
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { focusManager } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PRODUCTS, quoteFor } from "../e2e/fixtures/gateway.ts";
import { AppProviders } from "../src/shared/AppProviders.tsx";
import { CART_STORAGE_KEY, resetCartStore } from "../src/store/cart/cart-store.ts";
import { CUSTOMER_KEY, resetAttemptStore } from "../src/store/checkout/attempts.ts";
import { CLOSED_KEY, resetClosedStore } from "../src/store/checkout/closed.ts";
import { gateList, openGate, resetGate, type GateRecord } from "../src/store/checkout/gate.ts";
import type { CheckoutResult } from "../src/store/checkout/outcome.ts";
import { resetCheckoutFlow } from "../src/store/checkout/session.ts";
import { storeRoutes } from "../src/store/routes.tsx";

type Line = { product_id: string; quantity: number };
const CUSTOMER = "web-0123456789abcdefghijkl";
const LINES: Line[] = [
  { product_id: "sku-001", quantity: 1 },
  { product_id: "sku-002", quantity: 2 },
];

function result(ref: string, outcome: CheckoutResult["outcome"], extra: Partial<CheckoutResult> = {}): CheckoutResult {
  return {
    attempt_ref: ref,
    order_id: outcome === "abandoned" ? null : "order-0f8fad5b-d9cb-469f-a165-70867728950e",
    order_number: outcome === "abandoned" ? null : "OBS-1042-7F3A",
    state: "x",
    outcome,
    retryable: false,
    items: [
      { product_id: "sku-001", quantity: 1, unit_price_cents: 1200 },
      { product_id: "sku-002", quantity: 2, unit_price_cents: 1800 },
    ],
    total_cents: 4800,
    error_code: null,
    unavailable_items: null,
    created_at: "2026-10-25T12:00:00Z",
    ...extra,
  };
}

type Reply = () => Response | Promise<Response>;
const calls: string[] = [];

/** The Gateway: products, quotes, My Orders and Check status, each answered by its handler. */
function stubApi({ orders, get, resolve }: { orders?: Reply; get?: (ref: string) => Response | Promise<Response>; resolve?: (ref: string) => Response }) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input));
      const url = new URL(request.url, "http://127.0.0.1:8000");
      if (url.pathname === "/api/products") return Response.json(PRODUCTS);
      if (url.pathname === "/api/cart/quote") return Response.json(quoteFor(((await request.json()) as { items: Line[] }).items));
      calls.push(`${request.method} ${url.pathname}${url.search}`);
      if (url.pathname === "/api/orders" && request.method === "GET") return orders ? orders() : Response.json([]);
      const attempt = /^\/api\/attempts\/([^/]+)(\/resolve)?$/.exec(url.pathname);
      if (attempt?.[2] && resolve) return resolve(attempt[1] ?? "");
      if (attempt && !attempt[2] && get) return get(attempt[1] ?? "");
      return new Response("not in this test", { status: 503 });
    }),
  );
}

async function seedAttempt(attempt_ref: string, status: GateRecord["status"], created_at = "2026-10-26T12:00:00.000Z") {
  const db = await openGate();
  if (!db) throw new Error("no gate");
  await new Promise<void>((resolve) => {
    const tx = db.transaction("attempts", "readwrite");
    tx.objectStore("attempts").put({ attempt_ref, customer_id: CUSTOMER, lines: LINES, created_at, status });
    tx.oncomplete = () => resolve();
  });
  resetAttemptStore();
}

const held = async () => ((await gateList((await openGate()) as IDBDatabase)) ?? []).map((a) => a.attempt_ref);

function renderAt(path: string) {
  const router = createMemoryRouter(storeRoutes, { initialEntries: [path] });
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

const LIST = `GET /api/orders?customer_id=${CUSTOMER}&limit=50`;

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  resetGate();
  calls.length = 0;
  localStorage.clear();
  localStorage.setItem(CUSTOMER_KEY, CUSTOMER);
  resetCartStore();
  resetAttemptStore();
  resetClosedStore();
  resetCheckoutFlow();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetGate();
});

describe("My Orders list", () => {
  it("lists this customer's orders and the browser's unconfirmed attempts, one row each, newest first", async () => {
    await seedAttempt("ATT-7F3A-0915", "unknown");
    stubApi({
      orders: () =>
        Response.json([
          result("ATT-1A2B-0001", "confirmed", { created_at: "2026-10-25T12:00:00Z" }),
          result("ATT-1A2B-0002", "payment_failed", { order_number: "OBS-1031-9B04", error_code: "payment_declined", created_at: "2026-10-22T12:00:00Z" }),
        ]),
    });
    renderAt("/orders");
    expect(await screen.findByRole("heading", { level: 1, name: "My Orders" })).toBeTruthy();
    const table = await screen.findByRole("table", { name: "Your orders" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringMatching(/ATT-7F3A-0915.*Attempt reference.*3 items.*Unconfirmed.*Status unknown.*Check status/),
      expect.stringMatching(/OBS-1042-7F3A.*Oct 25, 2026.*3 items.*\$48\.00.*Confirmed.*View/),
      expect.stringMatching(/OBS-1031-9B04.*Attempt · not placed.*Oct 22, 2026.*\$48\.00.*Not charged.*Payment declined.*View/),
    ]);
    expect(screen.getByText("3 orders")).toBeTruthy();
    expect(calls).toEqual([LIST]);
    expect(within(rows[1] as HTMLElement).getByRole("link", { name: "View order OBS-1042-7F3A" }).getAttribute("href")).toBe("/orders/OBS-1042-7F3A");
  });

  it("is reached from the header", async () => {
    stubApi({});
    const router = renderAt("/");
    fireEvent.click(within(await screen.findByRole("navigation", { name: "Main" })).getByRole("link", { name: "My Orders" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/orders"));
  });

  it("empty: no orders yet, and a way back to the collection", async () => {
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders");
    expect(await screen.findByRole("heading", { level: 2, name: "No orders yet." })).toBeTruthy();
    expect(screen.getByText("Orders you place will appear here.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Shop the collection" }).getAttribute("href")).toBe("/#collection");
  });

  it("loading: placeholder rows, announced as loading", async () => {
    stubApi({ orders: () => new Promise<Response>(() => undefined) });
    renderAt("/orders");
    expect(await screen.findByText("Loading your orders…")).toBeTruthy();
    expect(screen.getByTestId("orders-loading").getAttribute("aria-busy")).toBe("true");
  });

  it("error: says so and Try again reloads; unconfirmed attempts stay listed and checkable", async () => {
    await seedAttempt("ATT-7F3A-0915", "unknown");
    let fail = true;
    stubApi({ orders: () => (fail ? Response.json({ error_code: "dependency_unavailable", message: "x" }, { status: 502 }) : Response.json([result("ATT-1A2B-0001", "confirmed")])) });
    renderAt("/orders");
    expect(await screen.findByRole("heading", { level: 2, name: "We couldn’t load your orders." })).toBeTruthy();
    expect(screen.getByText("ATT-7F3A-0915")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Check status" })).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("OBS-1042-7F3A")).toBeTruthy();
  });

  it("a failed refresh keeps the list (and every held attempt) on screen and says it could not refresh", async () => {
    await seedAttempt("ATT-7F3A-0915", "processing");
    let fail = false;
    stubApi({
      orders: () =>
        fail
          ? Response.json({ error_code: "dependency_unavailable", message: "x" }, { status: 502 })
          : Response.json([result("ATT-7F3A-0915", "processing", { order_number: "OBS-1039-2C11" }), result("ATT-1A2B-0001", "confirmed")]),
    });
    renderAt("/orders");
    expect(await screen.findByText("OBS-1039-2C11")).toBeTruthy();
    fail = true;
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    expect(await screen.findByText("We couldn’t refresh your orders.")).toBeTruthy();
    expect(screen.getByText("OBS-1039-2C11")).toBeTruthy();
    expect(screen.getByText("OBS-1042-7F3A")).toBeTruthy();
    fail = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.queryByText("We couldn’t refresh your orders.")).toBeNull());
  });

  it("50 orders back: says only the 50 most recent are shown, and an older unconfirmed attempt is still listed (U8)", async () => {
    await seedAttempt("ATT-0000-0001", "unknown", "2025-01-01T00:00:00.000Z");
    const fifty = Array.from({ length: 50 }, (_, i) => result(`ATT-1A2B-${String(i + 1).padStart(4, "0")}`, "confirmed", { order_number: `OBS-${2000 + i}-7F3A` }));
    stubApi({ orders: () => Response.json(fifty) });
    renderAt("/orders");
    expect(await screen.findByText("Showing your 50 most recent orders")).toBeTruthy();
    const table = screen.getByRole("table", { name: "Your orders" });
    expect(within(table).getAllByRole("row")).toHaveLength(52);
    expect(within(table).getByText("ATT-0000-0001")).toBeTruthy();
    expect(within(table).getByRole("button", { name: "Check status" })).toBeTruthy();
  });

  // The real list (GET /api/orders) has only checkouts that have an order: a fenced attempt never
  // appears in it, so every mock list below stays empty after the check.
  it("Check status on a row: GET first, then one resolve; the row stays as Not placed, from this browser, with the cart unchanged and a way to try again", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: LINES }));
    await seedAttempt("ATT-7F3A-0915", "unknown");
    stubApi({
      orders: () => Response.json([]),
      get: () => Response.json({ error_code: "attempt_not_found", message: "x" }, { status: 404 }),
      resolve: (ref) => Response.json({ error_code: "attempt_abandoned", message: "x", details: result(ref, "abandoned", { items: [], total_cents: null }) }, { status: 409 }),
    });
    renderAt("/orders");
    fireEvent.click(await screen.findByRole("button", { name: "Check status" }));
    const row = (await screen.findByText("Your cart is unchanged.")).closest("tr") as HTMLElement;
    expect(row.textContent).toMatch(/ATT-7F3A-0915.*Attempt · not placed.*3 items.*Not charged.*Not placed.*Your cart is unchanged\..*Try again.*Dismiss/);
    expect(row.textContent).not.toMatch(/OBS-/);
    expect(within(row).queryByRole("button", { name: "Check status" })).toBeNull();
    expect(within(row).getByRole("link", { name: "Try again: ATT-7F3A-0915" }).getAttribute("href")).toBe("/checkout");
    expect(screen.getByText("1 order")).toBeTruthy();
    expect(calls.filter((c) => c !== LIST)).toEqual([
      `GET /api/attempts/ATT-7F3A-0915?customer_id=${CUSTOMER}`,
      `POST /api/attempts/ATT-7F3A-0915/resolve?customer_id=${CUSTOMER}`,
    ]);
    expect(await held()).toEqual([]);
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual(LINES);
  });

  it("after a row's Check status settles it, focus moves to that row's View link and the result is announced", async () => {
    await seedAttempt("ATT-7F3A-0915", "unknown");
    let recorded = false;
    stubApi({
      orders: () => Response.json(recorded ? [result("ATT-7F3A-0915", "payment_failed", { order_number: "OBS-1031-9B04", error_code: "payment_declined" })] : []),
      get: (ref) => {
        recorded = true;
        return Response.json(result(ref, "payment_failed", { order_number: "OBS-1031-9B04", error_code: "payment_declined" }));
      },
    });
    renderAt("/orders");
    const check = await screen.findByRole("button", { name: "Check status" });
    check.focus();
    fireEvent.click(check);
    const view = await screen.findByRole("link", { name: "View order OBS-1031-9B04" });
    await waitFor(() => expect(document.activeElement).toBe(view));
    expect(screen.getByTestId("orders-announcer").textContent).toBe("ATT-7F3A-0915: Payment declined");
  });

  it("a never-placed result takes focus on the row's Try again link and is announced with the cart note", async () => {
    await seedAttempt("ATT-7F3A-0915", "unknown");
    stubApi({
      orders: () => Response.json([]),
      get: (ref) => Response.json(result(ref, "abandoned", { items: [], total_cents: null })),
    });
    renderAt("/orders");
    const check = await screen.findByRole("button", { name: "Check status" });
    check.focus();
    fireEvent.click(check);
    const again = await screen.findByRole("link", { name: "Try again: ATT-7F3A-0915" });
    await waitFor(() => expect(document.activeElement).toBe(again));
    expect(screen.getByTestId("orders-announcer").textContent).toBe("ATT-7F3A-0915: Not placed. Your cart is unchanged.");
  });

  it("a failed Check status says so on the row and never resolves", async () => {
    await seedAttempt("ATT-7F3A-0915", "unknown");
    stubApi({ orders: () => Response.json([]), get: () => Promise.reject(new TypeError("Failed to fetch")) });
    renderAt("/orders");
    fireEvent.click(await screen.findByRole("button", { name: "Check status" }));
    expect(await screen.findByText("Couldn’t check just now. Try again in a moment.")).toBeTruthy();
    expect(calls.filter((c) => c.includes("/resolve"))).toEqual([]);
    expect(await held()).toEqual(["ATT-7F3A-0915"]);
  });

  it("an attempt the server lists as confirmed is settled from the list: the cart loses those units once, nothing else is sent", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: [...LINES, { product_id: "sku-003", quantity: 1 }] }));
    resetCartStore();
    await seedAttempt("ATT-7F3A-0915", "unknown");
    stubApi({ orders: () => Response.json([result("ATT-7F3A-0915", "confirmed")]) });
    renderAt("/orders");
    expect(await screen.findByText("Confirmed")).toBeTruthy();
    await waitFor(async () => expect(await held()).toEqual([]));
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual([{ product_id: "sku-003", quantity: 1 }]);
    expect(calls.every((c) => c === LIST)).toBe(true);
  });
});

describe("order details", () => {
  it("confirmed: order number, date and units, the demo note, the lines and the total", async () => {
    stubApi({ orders: () => Response.json([result("ATT-1A2B-0001", "confirmed")]) });
    renderAt("/orders/OBS-1042-7F3A");
    expect(await screen.findByRole("heading", { level: 1, name: "Order" })).toBeTruthy();
    expect(screen.getByText("Confirmed")).toBeTruthy();
    expect(screen.getByText("OBS-1042-7F3A")).toBeTruthy();
    expect(screen.getByText("Oct 25, 2026 · 3 items")).toBeTruthy();
    expect(screen.getByText("Demo order. No payment was taken and nothing will be shipped.")).toBeTruthy();
    expect(screen.getByText("Qty 2 · $18.00 each")).toBeTruthy();
    expect(screen.getByTestId("order-total").textContent).toContain("$48.00");
    expect(screen.getByRole("link", { name: "All orders" }).getAttribute("href")).toBe("/orders");
  });

  it("unknown: an order attempt with Check status, which goes through GET first", async () => {
    await seedAttempt("ATT-7F3A-0915", "unknown");
    stubApi({ orders: () => Response.json([]), get: (ref) => Response.json(result(ref, "confirmed")) });
    renderAt("/orders/ATT-7F3A-0915");
    expect(await screen.findByRole("heading", { level: 1, name: "Order attempt" })).toBeTruthy();
    expect(screen.getByText(/We couldn’t confirm whether this order was placed/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Check status" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Order" })).toBeTruthy();
    expect(await screen.findByText("Confirmed")).toBeTruthy();
    expect(calls).toContain(`GET /api/attempts/ATT-7F3A-0915?customer_id=${CUSTOMER}`);
    expect(calls.filter((c) => c.includes("/resolve"))).toEqual([]);
  });

  it("declined: not placed, nothing charged", async () => {
    stubApi({ orders: () => Response.json([result("ATT-1A2B-0002", "payment_failed", { order_number: "OBS-1031-9B04", error_code: "payment_declined" })]) });
    renderAt("/orders/OBS-1031-9B04");
    expect(await screen.findByRole("heading", { level: 1, name: "Order attempt" })).toBeTruthy();
    expect(screen.getByText("Payment declined")).toBeTruthy();
    expect(screen.getByText(/wasn’t placed and nothing was charged/)).toBeTruthy();
  });

  it("processing (known to the server): says so, and Check status rereads the list", async () => {
    let done = false;
    stubApi({ orders: () => Response.json([result("ATT-1A2B-0003", done ? "confirmed" : "processing", { order_number: "OBS-1039-2C11" })]) });
    renderAt("/orders/OBS-1039-2C11");
    expect(await screen.findByText("Processing")).toBeTruthy();
    done = true;
    fireEvent.click(screen.getByRole("button", { name: "Check status" }));
    expect(await screen.findByText("Confirmed")).toBeTruthy();
    expect(calls.filter((c) => c.startsWith("GET /api/attempts") || c.includes("/resolve"))).toEqual([]);
  });

  it("if rereading the list fails after Check status settled the attempt, the page offers Try again (never stuck loading)", async () => {
    await seedAttempt("ATT-7F3A-0915", "unknown");
    let reads = 0;
    let gets = 0;
    stubApi({
      orders: () => (++reads === 1 ? Response.json([]) : Response.json({ error_code: "dependency_unavailable", message: "x" }, { status: 502 })),
      // The check gets its answer; the later read-only lookup fails too.
      get: (ref) =>
        ++gets === 1 ? Response.json(result(ref, "payment_failed", { order_number: null, error_code: "payment_declined" })) : Promise.reject(new TypeError("Failed to fetch")),
    });
    renderAt("/orders/ATT-7F3A-0915");
    fireEvent.click(await screen.findByRole("button", { name: "Check status" }));
    expect(await screen.findByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.queryByText("Loading your order…")).toBeNull();
  });

  it("an attempt reference neither listed nor held is looked up read-only; not found says so and never resolves", async () => {
    stubApi({ orders: () => Response.json([]), get: () => Response.json({ error_code: "attempt_not_found", message: "x" }, { status: 404 }) });
    renderAt("/orders/ATT-1A2B-0077");
    expect(await screen.findByRole("heading", { level: 1, name: "We couldn’t find this order." })).toBeTruthy();
    expect(calls).toEqual([LIST, `GET /api/attempts/ATT-1A2B-0077?customer_id=${CUSTOMER}`]);
  });

  it("an attempt reference found by that lookup is shown", async () => {
    stubApi({ orders: () => Response.json([]), get: (ref) => Response.json(result(ref, "failed", { order_number: null })) });
    renderAt("/orders/ATT-1A2B-0078");
    expect(await screen.findByText("Failed")).toBeTruthy();
    expect(screen.getByText("ATT-1A2B-0078")).toBeTruthy();
  });

  it("a malformed reference is not looked up", async () => {
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders/nonsense");
    expect(await screen.findByRole("heading", { level: 1, name: "We couldn’t find this order." })).toBeTruthy();
    await act(async () => undefined);
    expect(calls.filter((c) => c.includes("/api/attempts"))).toEqual([]);
  });
});

describe("My Orders and the checkout page", () => {
  it("a check started on My Orders never leaves its result on the checkout page", async () => {
    await seedAttempt("ATT-7F3A-0915", "unknown");
    let answer: (response: Response) => void = () => undefined;
    stubApi({ orders: () => Response.json([]), get: () => new Promise<Response>((resolve) => (answer = resolve)) });
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: LINES }));
    resetCartStore();
    const router = renderAt("/orders");
    fireEvent.click(await screen.findByRole("button", { name: "Check status" }));
    await waitFor(() => expect(calls.some((c) => c.startsWith("GET /api/attempts"))).toBe(true));
    await act(async () => {
      await router.navigate("/checkout");
    });
    await act(async () => answer(Response.json(result("ATT-7F3A-0915", "payment_failed", { error_code: "payment_declined" }))));
    expect(await screen.findByRole("heading", { level: 1, name: "Checkout" })).toBeTruthy();
    expect(screen.queryByRole("heading", { level: 1, name: "Payment didn’t go through." })).toBeNull();
  });
});

describe("after checkout", () => {
  it("the confirmation offers View My Orders", async () => {
    stubApi({ get: (ref) => Response.json(result(ref, "confirmed")) });
    renderAt("/checkout?attempt=ATT-1A2B-0001");
    expect(await screen.findByRole("heading", { level: 1, name: "Thank you." })).toBeTruthy();
    expect(screen.getByRole("link", { name: "View My Orders" }).getAttribute("href")).toBe("/orders");
  });
});

/** What another visit (or tab) left: the reminder for an attempt Check status found never placed. */
function leaveNote(ref = "ATT-7F3A-0915", customer = CUSTOMER) {
  const note = { attempt_ref: ref, customer_id: customer, lines: LINES, created_at: "2026-10-26T12:00:00.000Z", closed_at: new Date().toISOString() };
  const stored = JSON.parse(localStorage.getItem(CLOSED_KEY) ?? '{"version":1,"notes":[]}');
  localStorage.setItem(CLOSED_KEY, JSON.stringify({ version: 1, notes: [note, ...stored.notes] }));
  resetClosedStore();
}

describe("a never-placed attempt stays understandable (post-P4-I)", () => {
  it("after a reload the list still says Not placed, from this browser, and never as an order", async () => {
    leaveNote();
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders");
    const row = (await screen.findByText("Your cart is unchanged.")).closest("tr") as HTMLElement;
    expect(row.textContent).toMatch(/ATT-7F3A-0915.*Attempt · not placed.*Not placed.*Your cart is unchanged\..*Try again/);
    expect(row.textContent).not.toMatch(/OBS-/);
    expect(screen.queryByRole("heading", { level: 2, name: "No orders yet." })).toBeNull();
  });

  it("its details page says it was not placed, nothing was charged, the cart is unchanged and that no order was created", async () => {
    leaveNote();
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders/ATT-7F3A-0915");
    expect(await screen.findByRole("heading", { level: 1, name: "Order attempt" })).toBeTruthy();
    expect(screen.getByText("Not placed")).toBeTruthy();
    expect(screen.getByText(/nothing was charged\. Your cart is unchanged\./)).toBeTruthy();
    expect(screen.getByText(/kept in this browser only\. No order was created\./)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Try again" }).getAttribute("href")).toBe("/checkout");
    expect(screen.queryByRole("button", { name: "Check status" })).toBeNull();
    // Nothing is looked up or resolved for it: the reminder already says what is known.
    expect(calls.filter((c) => c.includes("/api/attempts"))).toEqual([]);
  });

  it("Try again goes to the checkout page, whose Place Order will use a new reference", async () => {
    leaveNote();
    stubApi({ orders: () => Response.json([]) });
    const router = renderAt("/orders");
    fireEvent.click(await screen.findByRole("link", { name: "Try again: ATT-7F3A-0915" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/checkout"));
    expect(router.state.location.search).toBe("");
  });

  it("Dismiss removes the row and the reminder; with nothing else, the page is empty again, also after a reload", async () => {
    leaveNote();
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders");
    fireEvent.click(await screen.findByRole("button", { name: "Dismiss ATT-7F3A-0915" }));
    expect(await screen.findByRole("heading", { level: 2, name: "No orders yet." })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(CLOSED_KEY) ?? "").notes).toEqual([]);
    cleanup();
    resetClosedStore();
    renderAt("/orders");
    expect(await screen.findByRole("heading", { level: 2, name: "No orders yet." })).toBeTruthy();
  });

  it("another tab's reminder appears here through the storage event", async () => {
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders");
    expect(await screen.findByRole("heading", { level: 2, name: "No orders yet." })).toBeTruthy();
    const note = { attempt_ref: "ATT-7F3A-0915", customer_id: CUSTOMER, lines: LINES, created_at: "2026-10-26T12:00:00.000Z", closed_at: new Date().toISOString() };
    localStorage.setItem(CLOSED_KEY, JSON.stringify({ version: 1, notes: [note] }));
    await act(async () => {
      window.dispatchEvent(new StorageEvent("storage", { key: CLOSED_KEY }));
    });
    expect(await screen.findByText("Your cart is unchanged.")).toBeTruthy();
  });

  it("a reminder for another customer ID is not shown", async () => {
    leaveNote("ATT-7F3A-0915", "web-zzzzzzzzzzzzzzzzzzzzzz");
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders");
    expect(await screen.findByRole("heading", { level: 2, name: "No orders yet." })).toBeTruthy();
  });

  it("if the server ever lists the reference, its answer is shown and the reminder is not", async () => {
    leaveNote("ATT-1A2B-0001");
    stubApi({ orders: () => Response.json([result("ATT-1A2B-0001", "confirmed")]) });
    renderAt("/orders");
    expect(await screen.findByText("OBS-1042-7F3A")).toBeTruthy();
    expect(screen.queryByText("Your cart is unchanged.")).toBeNull();
  });

  it("when the list cannot load, the reminder is still shown, apart from the unconfirmed attempts", async () => {
    leaveNote();
    await seedAttempt("ATT-7F3A-0916", "unknown");
    stubApi({ orders: () => Response.json({ error_code: "dependency_unavailable", message: "x" }, { status: 502 }) });
    renderAt("/orders");
    expect(await screen.findByRole("heading", { level: 2, name: "We couldn’t load your orders." })).toBeTruthy();
    const unconfirmed = screen.getByRole("heading", { level: 2, name: "Unconfirmed in this browser" }).closest("section") as HTMLElement;
    expect(unconfirmed.textContent).toContain("ATT-7F3A-0916");
    expect(unconfirmed.textContent).not.toContain("Your cart is unchanged.");
    const closed = screen.getByRole("heading", { level: 2, name: "Not placed in this browser" }).closest("section") as HTMLElement;
    expect(closed.textContent).toContain("ATT-7F3A-0915");
    expect(closed.textContent).toContain("Your cart is unchanged.");
  });

  it("a reminder for an attempt the browser still holds as unconfirmed does not hide its Check status", async () => {
    leaveNote("ATT-7F3A-0915");
    await seedAttempt("ATT-7F3A-0915", "unknown");
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders");
    expect(await screen.findByRole("button", { name: "Check status" })).toBeTruthy();
    expect(screen.queryByText("Your cart is unchanged.")).toBeNull();
  });
});

describe("a reminder that goes away while its page is open (review findings)", () => {
  it("dismissed elsewhere while its details page is open: the page does not hang on Loading, it says it cannot find the order", async () => {
    leaveNote();
    stubApi({ orders: () => Response.json([]), get: () => Response.json({ error_code: "attempt_not_found", message: "x" }, { status: 404 }) });
    renderAt("/orders/ATT-7F3A-0915");
    expect(await screen.findByRole("heading", { level: 1, name: "Order attempt" })).toBeTruthy();
    // Another tab dismisses it.
    localStorage.setItem(CLOSED_KEY, JSON.stringify({ version: 1, notes: [] }));
    await act(async () => {
      window.dispatchEvent(new StorageEvent("storage", { key: CLOSED_KEY }));
    });
    expect(await screen.findByRole("heading", { level: 1, name: "We couldn’t find this order." })).toBeTruthy();
    expect(screen.queryByText("Loading your order…")).toBeNull();
  });

  it("several reminders: each Try again link names its own attempt", async () => {
    leaveNote("ATT-7F3A-0915");
    leaveNote("ATT-7F3A-0916");
    stubApi({ orders: () => Response.json([]) });
    renderAt("/orders");
    expect(await screen.findByRole("link", { name: "Try again: ATT-7F3A-0915" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Try again: ATT-7F3A-0916" })).toBeTruthy();
  });
});
