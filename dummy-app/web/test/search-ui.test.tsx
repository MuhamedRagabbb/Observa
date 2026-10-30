// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/"}
// Product search (P4-G): the header's Search opens a field above the collection that filters it
// by display name. Case-insensitive, with a Clear control, a no-results state, Escape and a Close
// button, announced results, and the right navigation when opened from another page.
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PRODUCTS } from "../e2e/fixtures/gateway.ts";
import { AppProviders } from "../src/shared/AppProviders.tsx";
import { resetCartStore } from "../src/store/cart/cart-store.ts";
import { storeRoutes } from "../src/store/routes.tsx";

const scrollIntoView = vi.fn();

function stubApi(products: () => Promise<Response> | Response = () => Response.json(PRODUCTS)) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const request = input instanceof Request ? input : new Request(String(input), { method: "GET" });
      const path = new URL(request.url, "http://127.0.0.1:8000").pathname;
      if (path === "/api/products") return products();
      if (path === "/api/orders") return Response.json([]);
      return new Response("not in this test", { status: 503 });
    }),
  );
}

function renderAt(path = "/") {
  const router = createMemoryRouter(storeRoutes, { initialEntries: [path] });
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
  return router;
}

const banner = () => screen.getByRole("banner");
const searchButton = () => within(banner()).getByRole("button", { name: "Search" });
const collection = () => screen.getByRole("region", { name: "The Collection" });
const field = () => screen.getByRole("searchbox", { name: "Search products" });
const names = () => within(collection()).queryAllByRole("heading", { level: 3 }).map((h) => h.textContent);
const ALL = ["Journal / Notebook", "Coffee Mug", "Pen Set", "Limited Poster / Art Print", "Organizing Labels"];

/** Opens the field and waits for the products. */
async function openSearch() {
  await within(collection()).findAllByRole("listitem");
  fireEvent.click(searchButton());
  return field();
}

const type = (value: string) => fireEvent.change(field(), { target: { value } });

beforeEach(() => {
  localStorage.clear();
  resetCartStore();
  Element.prototype.scrollIntoView = scrollIntoView;
  scrollIntoView.mockClear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the Search control", () => {
  it("is a button in the header, closed to begin with, with no field on the page", async () => {
    stubApi();
    renderAt();
    expect(searchButton().getAttribute("aria-expanded")).toBe("false");
    expect(searchButton().getAttribute("aria-controls")).toBeNull();
    expect(screen.queryByRole("searchbox")).toBeNull();
    await within(collection()).findAllByRole("listitem");
    expect(names()).toEqual(ALL);
  });

  it("opens a labelled search field above the collection and moves focus into it", async () => {
    stubApi();
    renderAt();
    await openSearch();
    expect(screen.getByRole("search", { name: "Product search" })).toBeTruthy();
    expect(document.activeElement).toBe(field());
    expect(searchButton().getAttribute("aria-expanded")).toBe("true");
    expect(searchButton().getAttribute("aria-controls")).toBe(screen.getByRole("search", { name: "Product search" }).id);
    // The collection is brought into view, where the results appear.
    expect(scrollIntoView).toHaveBeenCalled();
  });

  it("pressing Search again while open focuses the field without clearing it", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("pen");
    searchButton().focus();
    fireEvent.click(searchButton());
    expect(document.activeElement).toBe(field());
    expect((field() as HTMLInputElement).value).toBe("pen");
  });
});

describe("filtering the collection", () => {
  it("filters as you type, ignoring case, and shows how many match", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("PEN");
    expect(names()).toEqual(["Pen Set"]);
    expect(within(collection()).getByText("1 of 5 objects")).toBeTruthy();
    type("o");
    expect(names()).toEqual(["Journal / Notebook", "Coffee Mug", "Limited Poster / Art Print", "Organizing Labels"]);
    expect(within(collection()).getByText("4 of 5 objects")).toBeTruthy();
  });

  it("every word has to match, in any order", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("notebook  journal");
    expect(names()).toEqual(["Journal / Notebook"]);
  });

  it("filtered products keep their cart action", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("mug");
    fireEvent.click(within(collection()).getByRole("button", { name: "Add to cart: Coffee Mug" }));
    expect(within(banner()).getByRole("button", { name: "Cart, 1 item" })).toBeTruthy();
  });

  it("a search typed while the products are loading applies when they arrive", async () => {
    let release: ((response: Response) => void) | null = null;
    stubApi(() => new Promise<Response>((resolve) => (release = resolve)));
    renderAt();
    fireEvent.click(searchButton());
    type("pen");
    await waitFor(() => expect(release).not.toBeNull());
    await act(async () => (release as unknown as (response: Response) => void)(Response.json(PRODUCTS)));
    await waitFor(() => expect(names()).toEqual(["Pen Set"]));
  });

  it("announces the number of matches in a live region, only after a pause in typing", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("pen");
    // Typing is not announced letter by letter: nothing is said until the pause.
    expect(screen.queryByText("1 of 5 objects match “pen”")).toBeNull();
    const said = await screen.findByText("1 of 5 objects match “pen”", {}, { timeout: 2000 });
    expect(said.getAttribute("role")).toBe("status");
    type("pe");
    type("coffee");
    expect(screen.queryByText("2 of 5 objects match “pe”")).toBeNull();
    expect(await screen.findByText("1 of 5 objects match “coffee”", {}, { timeout: 2000 })).toBeTruthy();
    expect(screen.queryByText("2 of 5 objects match “pe”")).toBeNull();
  });
});

describe("no results", () => {
  it("says so in the collection and in the live region, and Clear search brings everything back", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("zzz");
    expect(names()).toEqual([]);
    expect(within(collection()).getByText("No objects match “zzz”.")).toBeTruthy();
    expect(await screen.findByText("No objects match “zzz”", { exact: false, selector: "[role=status]" }, { timeout: 2000 })).toBeTruthy();
    // As in a browser, the clicked button takes focus, and it is gone once the products return.
    const clearSearch = within(collection()).getByRole("button", { name: "Clear search" });
    clearSearch.focus();
    fireEvent.click(clearSearch);
    expect((field() as HTMLInputElement).value).toBe("");
    expect(names()).toEqual(ALL);
    expect(document.activeElement).toBe(field());
  });

  it("a blank query is not a search: everything shows", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("   ");
    expect(names()).toEqual(ALL);
    expect(within(collection()).queryByText(/of 5 objects/)).toBeNull();
  });
});

describe("clearing and closing", () => {
  it("the Clear control appears only with text, empties the field and keeps focus in it", async () => {
    stubApi();
    renderAt();
    await openSearch();
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
    type("pen");
    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect((field() as HTMLInputElement).value).toBe("");
    expect(names()).toEqual(ALL);
    expect(document.activeElement).toBe(field());
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull();
  });

  it("Escape closes the search, clears the filter and returns focus to the Search button", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("pen");
    fireEvent.keyDown(field(), { key: "Escape" });
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(names()).toEqual(ALL);
    expect(document.activeElement).toBe(searchButton());
    expect(searchButton().getAttribute("aria-expanded")).toBe("false");
  });

  it("Escape closes the search from the Clear and Close buttons too", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("pen");
    screen.getByRole("button", { name: "Clear" }).focus();
    fireEvent.keyDown(screen.getByRole("button", { name: "Clear" }), { key: "Escape" });
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(document.activeElement).toBe(searchButton());
    fireEvent.click(searchButton());
    screen.getByRole("button", { name: "Close search" }).focus();
    fireEvent.keyDown(screen.getByRole("button", { name: "Close search" }), { key: "Escape" });
    expect(screen.queryByRole("searchbox")).toBeNull();
  });

  it("Escape while composing text (an input method) does not close the search", async () => {
    stubApi();
    renderAt();
    await openSearch();
    fireEvent.keyDown(field(), { key: "Escape", isComposing: true });
    expect(screen.queryByRole("searchbox")).not.toBeNull();
  });

  it("a Close button does the same, for people without an Escape key", async () => {
    stubApi();
    renderAt();
    await openSearch();
    type("pen");
    fireEvent.click(screen.getByRole("button", { name: "Close search" }));
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(names()).toEqual(ALL);
    expect(document.activeElement).toBe(searchButton());
  });

  it("pressing Enter in the field does not reload or leave the page", async () => {
    stubApi();
    const router = renderAt();
    await openSearch();
    type("pen");
    const form = screen.getByRole("search", { name: "Product search" });
    expect(fireEvent.submit(form)).toBe(false); // default prevented
    expect(router.state.location.pathname).toBe("/");
    expect(names()).toEqual(["Pen Set"]);
  });
});

describe("from another page", () => {
  it("opens from My Orders by going to the collection, with the field open and focused", async () => {
    stubApi();
    const router = renderAt("/orders");
    await screen.findByRole("heading", { level: 1, name: "My Orders" });
    fireEvent.click(searchButton());
    await waitFor(() => expect(router.state.location.pathname).toBe("/"));
    await waitFor(() => expect(document.activeElement).toBe(field()));
    expect(scrollIntoView).toHaveBeenCalled();
    // The request does not stay in the address or the history entry: Back and reload do not reopen it.
    expect(router.state.location.state).toBeNull();
    await within(collection()).findAllByRole("listitem");
    type("pen");
    expect(names()).toEqual(["Pen Set"]);
  });

  it("Back and Forward after arriving never reopen the search", async () => {
    stubApi();
    const router = renderAt("/orders");
    await screen.findByRole("heading", { level: 1, name: "My Orders" });
    fireEvent.click(searchButton());
    await waitFor(() => expect(document.activeElement).toBe(field()));
    fireEvent.keyDown(field(), { key: "Escape" });
    await act(async () => {
      await router.navigate(-1); // back to My Orders
    });
    expect(screen.queryByRole("searchbox")).toBeNull();
    await act(async () => {
      await router.navigate(1); // forward to the home page
    });
    await within(collection()).findAllByRole("listitem");
    expect(screen.queryByRole("searchbox")).toBeNull();
    expect(router.state.location.state).toBeNull();
  });

  it("leaving the home page closes the search and clears the filter", async () => {
    stubApi();
    const router = renderAt();
    await openSearch();
    type("pen");
    await act(async () => {
      await router.navigate("/orders");
    });
    await act(async () => {
      await router.navigate("/");
    });
    expect(screen.queryByRole("searchbox")).toBeNull();
    await within(collection()).findAllByRole("listitem");
    expect(names()).toEqual(ALL);
  });

  it("the checkout page's focused header has no Search", async () => {
    stubApi();
    renderAt("/checkout");
    expect(within(banner()).queryByRole("button", { name: "Search" })).toBeNull();
  });
});
