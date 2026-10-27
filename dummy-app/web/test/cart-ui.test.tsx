// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/"}
// The cart experience: Add to cart and Sold out on the collection, the header's Cart, and the
// cart dialog in every state. Prices and availability come only from POST /api/cart/quote.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PRODUCTS, quoteFor } from "../e2e/fixtures/gateway.ts";
import { AppProviders } from "../src/shared/AppProviders.tsx";
import { CART_STORAGE_KEY, resetCartStore } from "../src/store/cart/cart-store.ts";
import { storeRoutes } from "../src/store/routes.tsx";

type Line = { product_id: string; quantity: number };
type QuoteReply = (items: Line[]) => Promise<Response>;

const priced = (items: Line[]) => Response.json(quoteFor(items));

/** /api/products answers the catalogue; /api/cart/quote answers with `quote` (default: priced). */
function stubApi(quote: QuoteReply = async (items) => priced(items)) {
  const quoteBodies: Line[][] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input));
      const path = new URL(request.url, "http://127.0.0.1:8000").pathname;
      if (path === "/api/products") return Response.json(PRODUCTS);
      if (path === "/api/cart/quote") {
        const { items } = (await request.json()) as { items: Line[] };
        quoteBodies.push(items);
        return quote(items);
      }
      return new Response("not in this test", { status: 503 });
    }),
  );
  return quoteBodies;
}

function storeCart(lines: Line[]) {
  localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines }));
  resetCartStore();
}

function renderStore(path = "/") {
  const router = createMemoryRouter(storeRoutes, { initialEntries: [path] });
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

const cartButton = () => within(screen.getByRole("banner")).getByRole("button", { name: /^Cart,/ });
const dialog = () => screen.getByRole("dialog", { name: "Cart" });
async function openCart() {
  const button = cartButton();
  button.focus();
  fireEvent.click(button);
  return screen.findByRole("dialog", { name: "Cart" });
}

beforeEach(() => {
  localStorage.clear();
  resetCartStore();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Add to cart and Sold out", () => {
  it("adds one unit per press, shown in the header and kept for the next visit", async () => {
    stubApi();
    renderStore();
    const add = await screen.findByRole("button", { name: "Add to cart: Coffee Mug" });
    expect(cartButton().getAttribute("aria-label")).toBe("Cart, 0 items");
    fireEvent.click(add);
    fireEvent.click(add);
    expect(cartButton().getAttribute("aria-label")).toBe("Cart, 2 items");
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "")).toEqual({
      version: 1,
      lines: [{ product_id: "sku-002", quantity: 2 }],
    });
  });

  it("shows Sold out, disabled, for a product with no stock", async () => {
    stubApi();
    renderStore();
    const soldOut = await screen.findByRole("button", { name: "Sold out: Limited Poster / Art Print" });
    expect((soldOut as HTMLButtonElement).disabled).toBe(true);
    expect(soldOut.textContent).toContain("Sold out");
    expect(screen.queryByRole("button", { name: "Add to cart: Limited Poster / Art Print" })).toBeNull();
  });

  it("keeps focus on the card's button when the 10th unit is added", async () => {
    storeCart([{ product_id: "sku-002", quantity: 9 }]);
    stubApi();
    renderStore();
    const add = await screen.findByRole("button", { name: "Add to cart: Coffee Mug" });
    add.focus();
    fireEvent.click(add);
    const limit = screen.getByRole("button", { name: "10 in cart: Coffee Mug, the most per order" });
    expect(limit.getAttribute("aria-disabled")).toBe("true");
    expect(document.activeElement).toBe(limit);
    fireEvent.click(limit);
    expect(cartButton().getAttribute("aria-label")).toBe("Cart, 10 items");
  });

  it("says so when a product is at the limit of 10 in the cart", async () => {
    storeCart([{ product_id: "sku-002", quantity: 10 }]);
    stubApi();
    renderStore();
    const limit = await screen.findByRole("button", { name: "10 in cart: Coffee Mug, the most per order" });
    expect(limit.getAttribute("aria-disabled")).toBe("true");
  });

  it("says so when the cart already holds 10 different products", async () => {
    storeCart(Array.from({ length: 10 }, (_, i) => ({ product_id: `sku-${String(i + 100)}`, quantity: 1 })));
    stubApi();
    renderStore();
    const full = await screen.findByRole("button", { name: "Cart is full: 10 products, Coffee Mug not added" });
    expect(full.getAttribute("aria-disabled")).toBe("true");
  });
});

describe("the cart dialog", () => {
  it("opens as a modal labelled Cart, with focus moved inside", async () => {
    storeCart([{ product_id: "sku-001", quantity: 1 }]);
    stubApi();
    renderStore();
    const cart = await openCart();
    expect(cart.getAttribute("aria-modal")).toBe("true");
    expect(cart.contains(document.activeElement)).toBe(true);
  });

  it("closes on Escape and returns focus to the Cart button", async () => {
    stubApi();
    renderStore();
    await openCart();
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(cartButton());
  });

  it("returns focus only once the page is no longer inert (browsers refuse focus inside inert)", async () => {
    stubApi();
    renderStore();
    await openCart();
    const button = cartButton();
    const inertAtFocus: boolean[] = [];
    const focus = button.focus.bind(button);
    button.focus = (options?: FocusOptions) => {
      inertAtFocus.push(Boolean(button.closest("[inert]")));
      focus(options);
    };
    fireEvent.keyDown(document.activeElement as Element, { key: "Escape" });
    await waitFor(() => expect(document.activeElement).toBe(button));
    expect(inertAtFocus).toEqual([false]);
  });

  it("closes from its close button and returns focus to the Cart button", async () => {
    stubApi();
    renderStore();
    await openCart();
    fireEvent.click(within(dialog()).getByRole("button", { name: "Close cart" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(cartButton());
  });

  it("keeps Escape working after a click on a blank part of the cart", async () => {
    stubApi();
    renderStore();
    const cart = await openCart();
    fireEvent.mouseDown(cart);
    cart.focus();
    expect(document.activeElement).toBe(cart);
    fireEvent.keyDown(cart, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps Tab inside the dialog", async () => {
    storeCart([{ product_id: "sku-001", quantity: 2 }]);
    stubApi();
    renderStore();
    const cart = await openCart();
    await within(cart).findByText("Journal / Notebook");
    const focusable = [...cart.querySelectorAll<HTMLElement>("button:not([disabled]), a[href]")];
    const first = focusable[0] as HTMLElement;
    const last = focusable[focusable.length - 1] as HTMLElement;
    last.focus();
    fireEvent.keyDown(last, { key: "Tab" });
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(last);
  });

  it("makes the page behind it inert and stops it scrolling", async () => {
    stubApi();
    renderStore();
    await openCart();
    expect(screen.getByRole("banner", { hidden: true }).closest("[inert]")).not.toBeNull();
    expect(document.body.style.overflow).toBe("hidden");
    fireEvent.click(within(dialog()).getByRole("button", { name: "Close cart" }));
    expect(document.querySelector("[inert]")).toBeNull();
    expect(document.body.style.overflow).toBe("");
  });
});

describe("cart states", () => {
  it("ready: lines, prices and subtotal from the quote, with no checkout control yet", async () => {
    storeCart([
      { product_id: "sku-001", quantity: 1 },
      { product_id: "sku-002", quantity: 2 },
    ]);
    // Prices differ from the catalogue on purpose: the cart must show the quote's numbers.
    stubApi(async (items) => {
      const quote = quoteFor(items);
      quote.lines = quote.lines.map((l) => (l.product_id === "sku-002" ? { ...l, unit_price_cents: 2000, line_total_cents: 2000 * l.quantity } : l));
      quote.subtotal_cents = 1200 + 4000;
      return Response.json(quote);
    });
    renderStore();
    const cart = await openCart();
    const mug = (await within(cart).findByRole("heading", { name: "Coffee Mug" })).closest("li") as HTMLElement;
    expect(within(mug).getByText("2 × $20.00 each")).toBeTruthy();
    expect(within(mug).getByText("$40.00")).toBeTruthy();
    expect(within(cart).getByText("3 items")).toBeTruthy();
    expect(within(cart).getByText("$52.00")).toBeTruthy();
    expect(within(cart).getByText("Demo checkout. No real payment or shipping.")).toBeTruthy();
    expect(within(cart).queryByRole("button", { name: /checkout/i })).toBeNull();
    expect(within(cart).queryByRole("link", { name: /checkout/i })).toBeNull();
  });

  it("changing a quantity shows no out-of-date money while the new quote loads", async () => {
    storeCart([{ product_id: "sku-002", quantity: 1 }]);
    const pending: (() => void)[] = [];
    const bodies = stubApi(
      (items) =>
        new Promise<Response>((resolve) => {
          const reply = () => resolve(priced(items));
          if (items[0]?.quantity === 1) reply();
          else pending.push(reply);
        }),
    );
    renderStore();
    const cart = await openCart();
    const mug = (await within(cart).findByRole("heading", { name: "Coffee Mug" })).closest("li") as HTMLElement;
    expect(within(mug).getByText("$18.00")).toBeTruthy();
    fireEvent.click(within(cart).getByRole("button", { name: "Increase quantity of Coffee Mug" }));
    await waitFor(() => expect(bodies.at(-1)).toEqual([{ product_id: "sku-002", quantity: 2 }]));
    // Old money is never shown next to the new quantity.
    expect(within(cart).queryByText("$18.00")).toBeNull();
    expect(within(cart).getAllByText("Updating…").length).toBeGreaterThanOrEqual(2);
    act(() => pending.forEach((reply) => reply()));
    expect(await within(mug).findByText("$36.00")).toBeTruthy();
    expect(within(cart).getAllByText("$36.00")).toHaveLength(2); // the line and the subtotal
    expect(within(cart).queryByText("Updating…")).toBeNull();
    expect(cartButton().getAttribute("aria-label")).toBe("Cart, 2 items");
  });

  it("lets a line with too few in stock be lowered, but not one that is out of stock", async () => {
    storeCart([
      { product_id: "sku-001", quantity: 5 },
      { product_id: "sku-004", quantity: 1 },
    ]);
    stubApi(async (items) =>
      Response.json(
        quoteFor(items, [
          { product_id: "sku-001", name: "Notebook", price_cents: 1200, stock: 3 },
          { product_id: "sku-004", name: "Limited poster", price_cents: 900, stock: 0 },
        ]),
      ),
    );
    renderStore();
    const cart = await openCart();
    const notebook = (await within(cart).findByRole("heading", { name: "Journal / Notebook" })).closest("li") as HTMLElement;
    expect(within(notebook).getByText("Not enough in stock")).toBeTruthy();
    const lower = within(notebook).getByRole("button", { name: "Decrease quantity of Journal / Notebook" }) as HTMLButtonElement;
    expect(lower.disabled).toBe(false);
    const poster = within(cart).getByRole("heading", { name: "Limited Poster / Art Print" }).closest("li") as HTMLElement;
    expect((within(poster).getByRole("button", { name: /Decrease/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(lower);
    fireEvent.click(lower);
    await waitFor(() => expect(within(notebook).queryByText("Not enough in stock")).toBeNull());
    expect(within(notebook).getByText("3 × $12.00 each")).toBeTruthy();
  });

  it("removing a line moves focus to the cart's heading", async () => {
    storeCart([
      { product_id: "sku-001", quantity: 1 },
      { product_id: "sku-003", quantity: 1 },
    ]);
    stubApi();
    renderStore();
    const cart = await openCart();
    fireEvent.click(await within(cart).findByRole("button", { name: "Remove Pen Set" }));
    await waitFor(() => expect(within(cart).queryByText("Pen Set")).toBeNull());
    expect(document.activeElement).toBe(within(cart).getByRole("heading", { name: "Cart" }));
  });

  it("unavailable: says which line, explains, and removes unavailable lines on request", async () => {
    storeCart([
      { product_id: "sku-001", quantity: 1 },
      { product_id: "sku-004", quantity: 1 },
    ]);
    stubApi();
    renderStore();
    const cart = await openCart();
    const poster = (await within(cart).findByRole("heading", { name: "Limited Poster / Art Print" })).closest("li") as HTMLElement;
    expect(within(poster).getByText("Out of stock")).toBeTruthy();
    expect(within(poster).getByText("Unavailable")).toBeTruthy();
    expect(within(cart).getByText("1 item · 1 unavailable")).toBeTruthy();
    expect(within(cart).getByText("1 item is out of stock.")).toBeTruthy();
    expect(within(cart).getByText("Remove unavailable items to continue.")).toBeTruthy();
    fireEvent.click(within(cart).getByRole("button", { name: "Remove unavailable items" }));
    await waitFor(() => expect(within(cart).queryByText("Limited Poster / Art Print")).toBeNull());
    expect(within(cart).queryByText("Remove unavailable items to continue.")).toBeNull();
  });

  it("loading: says so while the quote is on its way", async () => {
    storeCart([{ product_id: "sku-001", quantity: 1 }]);
    stubApi(() => new Promise<Response>(() => undefined));
    renderStore();
    const cart = await openCart();
    expect(cart.getAttribute("aria-busy")).toBe("true");
    await waitFor(() => expect(within(cart).getByRole("status").textContent).toBe("Loading your cart…"));
  });

  it("error: explains and quotes again on Try again", async () => {
    storeCart([{ product_id: "sku-001", quantity: 1 }]);
    let failing = true;
    const bodies = stubApi(async (items) =>
      failing ? Response.json({ error_code: "dependency_unavailable", message: "down" }, { status: 503 }) : priced(items),
    );
    renderStore();
    const cart = await openCart();
    const alert = await within(cart).findByRole("alert");
    expect(alert.textContent).toContain("Your cart couldn’t load.");
    failing = false;
    const retry = within(alert).getByRole("button", { name: "Try again" });
    retry.focus();
    fireEvent.click(retry);
    expect(await within(cart).findByText("$12.00 each")).toBeTruthy();
    expect(bodies).toHaveLength(2);
    await waitFor(() => expect(document.activeElement).toBe(within(cart).getByRole("heading", { name: "Cart" })));
  });

  it("empty: invites a visit to the collection, which closes the cart and moves focus there", async () => {
    const bodies = stubApi();
    const router = renderStore();
    const cart = await openCart();
    expect(within(cart).getByText("Your cart is empty.")).toBeTruthy();
    fireEvent.click(within(cart).getByRole("link", { name: "Shop the collection" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(router.state.location.hash).toBe("#collection");
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("heading", { level: 2, name: "The Collection" })),
    );
    expect(bodies).toEqual([]);
  });
});

describe("other tabs", () => {
  it("follows a cart changed in another tab", async () => {
    stubApi();
    renderStore();
    await screen.findByRole("button", { name: "Add to cart: Coffee Mug" });
    const value = JSON.stringify({ version: 1, lines: [{ product_id: "sku-003", quantity: 4 }] });
    localStorage.setItem(CART_STORAGE_KEY, value);
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: CART_STORAGE_KEY, newValue: value }));
    });
    expect(cartButton().getAttribute("aria-label")).toBe("Cart, 4 items");
  });
});
