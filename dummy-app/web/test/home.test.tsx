// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/"}
// The storefront home: the hero and "The Collection", read from GET /api/products. Names and
// images come from the display map; prices, stock and IDs only from the API.
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { focusManager } from "@tanstack/react-query";
import { act } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PRODUCTS, type Product } from "../e2e/fixtures/gateway.ts";
import { AppProviders } from "../src/shared/AppProviders.tsx";
import { storeRoutes } from "../src/store/routes.tsx";

type Reply = () => Promise<Response>;

/** Answers /api/products with the given replies in turn (the last one repeats); /ui/config fails. */
function stubApi(...replies: Reply[]) {
  const productCalls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = input instanceof Request ? input.url : String(input);
      if (!url.includes("/api/products")) return new Response("not in this test", { status: 503 });
      productCalls.push(url);
      const reply = replies[Math.min(productCalls.length, replies.length) - 1] as Reply;
      return reply();
    }),
  );
  return productCalls;
}

const json = (body: unknown, status = 200) => async () => Response.json(body, { status });

function renderHome(path = "/") {
  const router = createMemoryRouter(storeRoutes, { initialEntries: [path] });
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

const collection = () => screen.getByRole("region", { name: "The Collection" });

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("hero", () => {
  it("opens with the headline and a link down to the collection", () => {
    stubApi(json(PRODUCTS));
    renderHome();
    expect(screen.getByRole("heading", { level: 1, name: "Quiet objects, closely observed." })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Shop the collection" }).getAttribute("href")).toBe("/#collection");
  });
});

describe("the collection", () => {
  it("lists the API's products in order, with artboard names and API prices", async () => {
    // Prices differ from the seed on purpose: the page must show what the API says.
    const products: Product[] = PRODUCTS.map((p) => (p.product_id === "sku-002" ? { ...p, price_cents: 2450 } : p));
    stubApi(json(products));
    renderHome();
    const items = await within(collection()).findAllByRole("listitem");
    expect(items.map((item) => within(item).getByRole("heading").textContent)).toEqual([
      "Journal / Notebook",
      "Coffee Mug",
      "Pen Set",
      "Limited Poster / Art Print",
      "Organizing Labels",
    ]);
    expect(items.map((item) => within(item).getByText(/^\$\d/).textContent)).toEqual([
      "$12.00",
      "$24.50",
      "$6.00",
      "$9.00",
      "$3.00",
    ]);
    expect(within(collection()).getByText("5 objects")).toBeTruthy();
  });

  it("marks availability from real stock: zero is out of stock", async () => {
    const products: Product[] = PRODUCTS.map((p) => (p.product_id === "sku-001" ? { ...p, stock: 0 } : p));
    stubApi(json(products));
    renderHome();
    const items = await within(collection()).findAllByRole("listitem");
    expect(items.map((item) => (within(item).queryByText("Out of stock") ? "out" : within(item).getByText("In stock") && "in"))).toEqual([
      "out",
      "in",
      "in",
      "out",
      "in",
    ]);
  });

  it("offers no cart controls yet: the cart arrives in P4-C", async () => {
    stubApi(json(PRODUCTS));
    renderHome();
    await within(collection()).findAllByRole("listitem");
    expect(within(collection()).queryAllByRole("button")).toEqual([]);
    expect(within(collection()).queryByText(/add to cart|sold out/i)).toBeNull();
  });

  it("shows a product the display map does not know under its API name", async () => {
    stubApi(json([{ product_id: "sku-077", name: "Desk lamp", price_cents: 4200, stock: 3 }]));
    renderHome();
    const [item] = await within(collection()).findAllByRole("listitem");
    expect(within(item as HTMLElement).getByRole("heading").textContent).toBe("Desk lamp");
    expect(within(collection()).getByText("1 object")).toBeTruthy();
  });

  it("says so while the products load, from a status region that stays in place", async () => {
    let finish: (response: Response) => void = () => undefined;
    const calls = stubApi(() => new Promise<Response>((resolve) => (finish = resolve)));
    renderHome();
    await waitFor(() => expect(calls).toHaveLength(1));
    const region = collection();
    expect(region.getAttribute("aria-busy")).toBe("true");
    const status = within(region).getByRole("status");
    expect(status.textContent).toBe("Loading the collection…");
    expect(within(region).queryByText(/objects?$/)).toBeNull();
    expect(within(region).queryByText("Journal / Notebook")).toBeNull();
    await act(async () => finish(Response.json(PRODUCTS)));
    await within(region).findAllByRole("listitem");
    // The same region, now quiet: screen readers announce changes to a region already present.
    expect(within(region).getByRole("status")).toBe(status);
    expect(status.textContent).toBe("");
  });

  it("keeps showing the products when a background refresh fails", async () => {
    let failing = false;
    stubApi(async () =>
      failing ? Response.json({ error_code: "dependency_unavailable", message: "down" }, { status: 503 }) : Response.json(PRODUCTS),
    );
    renderHome();
    await within(collection()).findAllByRole("listitem");
    failing = true;
    // Returning to the tab refreshes the catalogue in the background.
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await waitFor(() => expect(collection().getAttribute("aria-busy")).toBeNull());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(within(collection()).getAllByRole("listitem")).toHaveLength(5);
    expect(within(collection()).getByText("5 objects")).toBeTruthy();
    expect(within(collection()).queryByRole("alert")).toBeNull();
    focusManager.setFocused(undefined);
  });

  it("says so when there is nothing to show", async () => {
    stubApi(json([]));
    renderHome();
    expect(await within(collection()).findByText("No objects are available right now.")).toBeTruthy();
    expect(within(collection()).queryByRole("list")).toBeNull();
  });

  it("explains a failure and tries again on request", async () => {
    const calls = stubApi(
      json({ error_code: "dependency_unavailable", message: "Inventory is down" }, 503),
      json(PRODUCTS),
    );
    renderHome();
    const alert = await within(collection()).findByRole("alert");
    expect(alert.textContent).toContain("The collection didn’t load.");
    expect(within(collection()).queryByRole("list")).toBeNull();
    const retry = within(collection()).getByRole("button", { name: "Try again" });
    retry.focus();
    fireEvent.click(retry);
    expect(await within(collection()).findAllByRole("listitem")).toHaveLength(5);
    expect(calls).toHaveLength(2);
    // The button is gone; focus lands on the collection heading instead of the page body.
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { level: 2, name: "The Collection" })));
  });
});

describe("collection anchor", () => {
  it("moves focus to the collection heading when the page opens at #collection", async () => {
    stubApi(json(PRODUCTS));
    renderHome("/#collection");
    const heading = screen.getByRole("heading", { level: 2, name: "The Collection" });
    await waitFor(() => expect(document.activeElement).toBe(heading));
  });
});
