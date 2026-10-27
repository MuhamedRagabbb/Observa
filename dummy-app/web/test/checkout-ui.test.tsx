// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/"}
// The checkout page, its outcomes and the cart's Proceed to Checkout. Prices come from the
// quote (form) or the checkout result (outcomes); every POST is counted.
import { focusManager } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PRODUCTS, quoteFor } from "../e2e/fixtures/gateway.ts";
import { AppProviders } from "../src/shared/AppProviders.tsx";
import { CART_STORAGE_KEY, resetCartStore } from "../src/store/cart/cart-store.ts";
import { ATTEMPTS_KEY, resetAttemptStore } from "../src/store/checkout/attempts.ts";
import { resetCheckoutFlow } from "../src/store/checkout/session.ts";
import type { CheckoutResult } from "../src/store/checkout/outcome.ts";
import { storeRoutes } from "../src/store/routes.tsx";

type Line = { product_id: string; quantity: number };
type CheckoutReply = (ref: string, body: { items: Line[] }) => Response | Promise<Response>;

const LINES: Line[] = [
  { product_id: "sku-001", quantity: 1 },
  { product_id: "sku-002", quantity: 2 },
];

function result(ref: string, outcome: CheckoutResult["outcome"], extra: Partial<CheckoutResult> = {}): CheckoutResult {
  return {
    attempt_ref: ref,
    order_id: outcome === "confirmed" ? "order-0f8fad5b-d9cb-469f-a165-70867728950e" : null,
    order_number: outcome === "confirmed" ? "OBS-1042-7F3A" : null,
    state: "x",
    outcome,
    retryable: outcome !== "confirmed" && outcome !== "unavailable",
    items: [
      { product_id: "sku-001", quantity: 1, unit_price_cents: 1200 },
      { product_id: "sku-002", quantity: 2, unit_price_cents: 1800 },
    ],
    total_cents: 4800,
    error_code: null,
    unavailable_items: null,
    created_at: null,
    ...extra,
  };
}

const failure = (status: number, code: string, details: CheckoutResult) =>
  Response.json({ error_code: code, message: "x", details }, { status });

function stubApi(checkout: CheckoutReply, quote: (items: Line[]) => Promise<Response> = async (items) => Response.json(quoteFor(items))) {
  const posts: { ref: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input));
      const path = new URL(request.url, "http://127.0.0.1:8000").pathname;
      if (path === "/api/products") return Response.json(PRODUCTS);
      if (path === "/api/cart/quote") return quote(((await request.json()) as { items: Line[] }).items);
      if (path === "/api/checkout") {
        const body = (await request.json()) as Record<string, unknown>;
        const ref = request.headers.get("Idempotency-Key") ?? "";
        posts.push({ ref, body });
        return checkout(ref, body as { items: Line[] });
      }
      return new Response("not in this test", { status: 503 });
    }),
  );
  return posts;
}

function storeCart(lines: Line[]) {
  localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines }));
  resetCartStore();
}

function renderAt(path: string) {
  const router = createMemoryRouter(storeRoutes, { initialEntries: [path] });
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

async function fillAndPlace() {
  fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Demo Name" } });
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: "demo@example.com" } });
  const place = await screen.findByRole("button", { name: /^Place Order/ });
  await waitFor(() => expect(place.getAttribute("aria-disabled")).toBeNull());
  fireEvent.click(place);
  return place;
}

beforeEach(() => {
  localStorage.clear();
  resetCartStore();
  resetAttemptStore();
  resetCheckoutFlow();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Proceed to Checkout in the cart", () => {
  async function openCart() {
    const button = await within(screen.getByRole("banner")).findByRole("button", { name: /^Cart,/ });
    fireEvent.click(button);
    return screen.findByRole("dialog", { name: "Cart" });
  }

  it("leads to the checkout page when the cart can be checked out", async () => {
    storeCart(LINES);
    stubApi(() => new Response(null, { status: 500 }));
    const router = renderAt("/");
    const cart = await openCart();
    const proceed = await within(cart).findByRole("link", { name: "Proceed to Checkout" });
    fireEvent.click(proceed);
    await waitFor(() => expect(router.state.location.pathname).toBe("/checkout"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("is disabled with an unavailable item; counts: header = all units, cart = units that can be bought", async () => {
    storeCart([...LINES, { product_id: "sku-004", quantity: 2 }]);
    stubApi(() => new Response(null, { status: 500 }));
    renderAt("/");
    const cart = await openCart();
    expect(await within(cart).findByRole("button", { name: "Proceed to Checkout" })).toHaveProperty("disabled", true);
    expect(within(cart).queryByRole("link", { name: "Proceed to Checkout" })).toBeNull();
    // 5 units in the cart, 3 of them can be bought; the subtotal leaves the poster out.
    expect(within(screen.getByRole("banner", { hidden: true })).getByRole("button", { name: "Cart, 5 items", hidden: true })).toBeTruthy();
    expect(within(cart).getByText("3 items · 1 unavailable")).toBeTruthy();
    expect(within(cart).getByText("$48.00")).toBeTruthy();
  });

  it("is disabled while an earlier attempt is unconfirmed, and says why", async () => {
    storeCart(LINES);
    localStorage.setItem(
      ATTEMPTS_KEY,
      JSON.stringify({ version: 1, attempts: [{ attempt_ref: "ATT-1A2B-0001", lines: LINES, created_at: "2026-10-27T10:00:00.000Z", status: "unknown" }] }),
    );
    resetAttemptStore();
    stubApi(() => new Response(null, { status: 500 }));
    renderAt("/");
    const cart = await openCart();
    expect(await within(cart).findByRole("button", { name: "Proceed to Checkout" })).toHaveProperty("disabled", true);
    expect(within(cart).getByText(/ATT-1A2B-0001/)).toBeTruthy();
  });
});

describe("checkout form", () => {
  it("shows the server-priced summary and the total on Place Order", async () => {
    storeCart(LINES);
    stubApi(() => new Response(null, { status: 500 }), async (items) => {
      const quote = quoteFor(items);
      quote.lines = quote.lines.map((l) => (l.product_id === "sku-002" ? { ...l, unit_price_cents: 2000, line_total_cents: 4000 } : l));
      quote.subtotal_cents = 5200;
      return Response.json(quote);
    });
    renderAt("/checkout");
    expect(await screen.findByRole("heading", { level: 1, name: "Checkout" })).toBeTruthy();
    const summary = screen.getAllByRole("region", { name: "Order summary" })[0] as HTMLElement;
    expect(await within(summary).findByText("Qty 2 · $20.00 each")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Place Order · $52.00" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Return to cart" })).toBeTruthy();
  });

  it("does not let Place Order send while the quote does not yet match the cart", async () => {
    storeCart(LINES);
    const posts = stubApi(() => new Response(null, { status: 500 }), () => new Promise<Response>(() => undefined));
    renderAt("/checkout");
    fireEvent.change(await screen.findByLabelText("Full name"), { target: { value: "Demo Name" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "demo@example.com" } });
    const place = screen.getByRole("button", { name: /^Place Order/ });
    expect(place.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(place);
    expect(posts).toEqual([]);
  });

  it("keeps Place Order unavailable when refreshing the prices fails, even with older prices cached", async () => {
    storeCart(LINES);
    let failing = false;
    const posts = stubApi(
      () => new Response(null, { status: 500 }),
      async (items) => (failing ? Response.json({ error_code: "dependency_unavailable", message: "x" }, { status: 503 }) : Response.json(quoteFor(items))),
    );
    renderAt("/checkout");
    const place = await screen.findByRole("button", { name: /^Place Order · / });
    await waitFor(() => expect(place.getAttribute("aria-disabled")).toBeNull());
    failing = true;
    // Returning to the tab refreshes the prices; this time the refresh fails.
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    expect(await screen.findByText(/Prices couldn’t load/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /^Place Order/ }).getAttribute("aria-disabled")).toBe("true");
    fireEvent.change(screen.getByLabelText("Full name"), { target: { value: "Demo Name" } });
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "demo@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: /^Place Order/ }));
    expect(posts).toEqual([]);
    focusManager.setFocused(undefined);
  });

  it("checks contact details with the backend's rules and sends nothing until they pass", async () => {
    storeCart(LINES);
    const posts = stubApi(() => new Response(null, { status: 500 }));
    renderAt("/checkout");
    fireEvent.change(await screen.findByLabelText("Email"), { target: { value: "demo@example" } });
    const place = await screen.findByRole("button", { name: /^Place Order/ });
    await waitFor(() => expect(place.getAttribute("aria-disabled")).toBeNull());
    fireEvent.click(place);
    const name = screen.getByLabelText("Full name");
    expect(name.getAttribute("aria-invalid")).toBe("true");
    expect(screen.getByText("Enter a name.")).toBeTruthy();
    expect(screen.getByText("Enter an email like demo@example.com.")).toBeTruthy();
    expect(document.activeElement).toBe(name);
    expect(posts).toEqual([]);
  });

  it("while the order is placed: busy button, a status line, and one POST for two presses", async () => {
    storeCart(LINES);
    const posts = stubApi(() => new Promise<Response>(() => undefined));
    renderAt("/checkout");
    const place = await fillAndPlace();
    fireEvent.click(place);
    expect(await screen.findByText("Submitting your order…")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Placing order/ }).getAttribute("aria-busy")).toBe("true");
    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]?.body).toMatchObject({ items: LINES, contact: { name: "Demo Name", email: "demo@example.com" } });
  });

  it("shows the empty state, with nothing to submit, when the cart is empty", async () => {
    stubApi(() => new Response(null, { status: 500 }));
    renderAt("/checkout");
    expect(await screen.findByText("Your cart is empty.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Place Order/ })).toBeNull();
  });
});

describe("definite outcomes", () => {
  it("confirmed: the real order number, the cart cleared, Continue shopping", async () => {
    storeCart(LINES);
    stubApi((ref) => Response.json(result(ref, "confirmed"), { status: 201 }));
    renderAt("/checkout");
    await fillAndPlace();
    expect(await screen.findByRole("heading", { level: 1, name: "Thank you." })).toBeTruthy();
    expect(screen.getByText("OBS-1042-7F3A")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy order ID" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Continue shopping" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /My Orders/ })).toBeNull();
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual([]);
    expect(JSON.parse(localStorage.getItem(ATTEMPTS_KEY) ?? "").attempts).toEqual([]);
  });

  it("payment declined: nothing charged, Try again sends a new attempt", async () => {
    storeCart(LINES);
    const posts = stubApi((ref) => failure(402, "payment_declined", result(ref, "payment_failed", { error_code: "payment_declined" })));
    renderAt("/checkout");
    await fillAndPlace();
    expect(await screen.findByRole("heading", { level: 1, name: "Payment didn’t go through." })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual(LINES);
    // Try again returns to the form (details kept) so a fresh quote backs the next attempt.
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Checkout" })).toBeTruthy();
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe("demo@example.com");
    expect(posts).toHaveLength(1);
    const place = await screen.findByRole("button", { name: /^Place Order/ });
    await waitFor(() => expect(place.getAttribute("aria-disabled")).toBeNull());
    fireEvent.click(place);
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.ref).not.toBe(posts[0]?.ref);
  });

  it("payment unavailable: says the payment service was unavailable", async () => {
    storeCart(LINES);
    stubApi((ref) => failure(503, "payment_unavailable", result(ref, "payment_failed", { error_code: "payment_unavailable" })));
    renderAt("/checkout");
    await fillAndPlace();
    expect(await screen.findByRole("heading", { level: 1, name: "Payment couldn’t be completed." })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });

  it("not placed: names the unavailable product and offers Review cart, which opens the cart", async () => {
    storeCart(LINES);
    stubApi((ref) =>
      failure(409, "insufficient_stock", result(ref, "unavailable", { error_code: "insufficient_stock", unavailable_items: [{ product_id: "sku-002" }] })),
    );
    const router = renderAt("/checkout");
    await fillAndPlace();
    expect(await screen.findByRole("heading", { level: 1, name: "We couldn’t place your order." })).toBeTruthy();
    expect(screen.getByText("Coffee Mug", { selector: "strong" })).toBeTruthy();
    expect(screen.getAllByText("No longer available").length).toBeGreaterThan(0);
    // The summary describes the attempt as sent: its units agree with the result's total.
    const summary = screen.getAllByRole("region", { name: "Order summary" })[0] as HTMLElement;
    expect(within(summary).getByText("Subtotal · 3 items")).toBeTruthy();
    expect(within(summary).getAllByText("$48.00").length).toBe(2);
    fireEvent.click(screen.getByRole("link", { name: "Review cart" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    expect(await screen.findByRole("dialog", { name: "Cart" })).toBeTruthy();
  });

  it("failed and not retryable: Return to cart only", async () => {
    storeCart(LINES);
    stubApi((ref) => failure(500, "order_persistence_error", result(ref, "failed", { retryable: false, error_code: "order_persistence_error" })));
    renderAt("/checkout");
    await fillAndPlace();
    expect(await screen.findByRole("heading", { level: 1, name: "Something went wrong." })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("unknown outcome (kept for P4-E)", () => {
  it("network error: shows the reference, keeps the record and sends nothing more", async () => {
    storeCart(LINES);
    const posts = stubApi(() => Promise.reject(new TypeError("Failed to fetch")));
    renderAt("/checkout");
    await fillAndPlace();
    expect(await screen.findByRole("heading", { level: 1, name: "We couldn’t confirm your order." })).toBeTruthy();
    const ref = posts[0]?.ref ?? "";
    expect(screen.getByText(ref)).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(ATTEMPTS_KEY) ?? "").attempts[0]).toMatchObject({ attempt_ref: ref, status: "unknown" });
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual(LINES);
    expect(screen.queryByRole("button", { name: /Place Order|Try again|Check/ })).toBeNull();
    expect(posts).toHaveLength(1);
  });

  it("an unconfirmed attempt found on arrival (reload, another tab) shows that attempt instead of the form", async () => {
    storeCart(LINES);
    localStorage.setItem(
      ATTEMPTS_KEY,
      JSON.stringify({ version: 1, attempts: [{ attempt_ref: "ATT-1A2B-0009", lines: LINES, created_at: "2026-10-27T10:00:00.000Z", status: "submitting" }] }),
    );
    resetAttemptStore();
    const posts = stubApi(() => new Response(null, { status: 500 }));
    renderAt("/checkout");
    // A "submitting" record may still be in flight in another tab: the copy says only what is known.
    expect(await screen.findByRole("heading", { level: 1, name: "We’re still waiting to confirm your order." })).toBeTruthy();
    expect(screen.getByText(/haven’t had an answer/)).toBeTruthy();
    expect(screen.getByText("ATT-1A2B-0009")).toBeTruthy();
    expect(screen.queryByLabelText("Full name")).toBeNull();
    expect(posts).toEqual([]);
  });

  it("202 processing: says it is still processing and keeps the record", async () => {
    storeCart(LINES);
    stubApi((ref) => Response.json(result(ref, "processing"), { status: 202 }));
    renderAt("/checkout");
    await fillAndPlace();
    expect(await screen.findByRole("heading", { level: 1, name: "Your order is still processing." })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem(ATTEMPTS_KEY) ?? "").attempts[0]).toMatchObject({ status: "processing" });
  });
});
