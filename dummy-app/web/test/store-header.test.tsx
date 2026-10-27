// The storefront header (Hero and My Orders artboards) and its focused variant (Checkout).
// Items appear only when the feature behind them works: P4-A ships Home and Experiments.
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, MemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppProviders } from "../src/shared/AppProviders.tsx";
import { StoreHeader } from "../src/store/StoreHeader.tsx";
import { storeRoutes } from "../src/store/routes.tsx";

beforeEach(() => {
  vi.stubGlobal("fetch", async () => new Response("unavailable in tests", { status: 503 }));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderAt(routes: RouteObject[], path = "/") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

const headerAt = (element: React.ReactNode, path = "/") =>
  renderAt([{ path: "*", element }], path);

describe("storefront header, as shipped in P4-A", () => {
  it("links the wordmark to the storefront home", () => {
    renderAt(storeRoutes);
    const banner = screen.getByRole("banner");
    const home = within(banner).getByRole("link", { name: "OBSERVA" });
    expect(home.getAttribute("href")).toBe("/");
  });

  it("marks Home as the current page in the main navigation", () => {
    renderAt(storeRoutes);
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Home" }).getAttribute("aria-current")).toBe("page");
  });

  it("links Products to the collection on the home page, without marking it current", () => {
    renderAt(storeRoutes);
    const nav = screen.getByRole("navigation", { name: "Main" });
    const products = within(nav).getByRole("link", { name: "Products" });
    expect(products.getAttribute("href")).toBe("/#collection");
    expect(products.getAttribute("aria-current")).toBeNull();
  });

  it("offers exactly the controls that work, and nothing else", async () => {
    vi.stubGlobal("fetch", async () =>
      Response.json({ mode: "store", ports: { store: 18000, control: 18090, grafana: 1, jaeger: 1, prometheus: 1 } }),
    );
    renderAt(storeRoutes);
    const banner = screen.getByRole("banner");
    await within(banner).findByRole("link", { name: "Experiments" });
    const interactive = "a, button, input, select, textarea, summary, [tabindex], [role='button'], [role='link']";
    const names = [...banner.querySelectorAll(interactive)].map(
      (el) =>
        el.getAttribute("aria-label") ??
        el.querySelector("svg[aria-label]")?.getAttribute("aria-label") ??
        el.textContent?.replace("↗", "").trim(),
    );
    // My Orders twice: in the main navigation (wide screens) and beside the cart (phones only, by CSS).
    expect(names).toEqual(["OBSERVA", "Home", "Products", "My Orders", "Experiments", "My Orders", "Cart, 0 items"]);
  });
});

describe("StoreHeader slots for later slices", () => {
  it("adds navigation links in order and marks only the current one", () => {
    headerAt(
      <StoreHeader
        links={[
          { to: "/", label: "Home", end: true },
          { to: "/orders", label: "My Orders" },
        ]}
      />,
      "/orders",
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(["Home", "My Orders"]);
    expect(links[0]?.getAttribute("aria-current")).toBeNull();
    expect(links[1]?.getAttribute("aria-current")).toBe("page");
  });

  it("shows Search only when given something to open", () => {
    const onOpen = vi.fn();
    headerAt(<StoreHeader links={[]} search={{ onOpen }} />);
    fireEvent.click(screen.getByRole("button", { name: "Search" }));
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("shows the Cart with its count in words", () => {
    const onOpen = vi.fn();
    headerAt(<StoreHeader links={[]} cart={{ count: 2, onOpen }} />);
    const cart = screen.getByRole("button", { name: "Cart, 2 items" });
    fireEvent.click(cart);
    expect(onOpen).toHaveBeenCalledTimes(1);
  });

  it("announces a change in the cart count from one live region outside the button", () => {
    const header = (count: number) => <StoreHeader links={[]} cart={{ count, onOpen: vi.fn() }} />;
    const { rerender } = render(<AppProviders><MemoryRouter>{header(0)}</MemoryRouter></AppProviders>);
    const region = screen.getByRole("status");
    expect(region.getAttribute("aria-live")).toBe("polite");
    expect(region.closest("button")).toBeNull();
    rerender(<AppProviders><MemoryRouter>{header(3)}</MemoryRouter></AppProviders>);
    expect(screen.getByRole("status")).toBe(region);
    expect(region.textContent).toBe("Cart, 3 items");
  });
});

describe("focused header (checkout and outcomes)", () => {
  it("replaces the navigation with one way back", () => {
    headerAt(<StoreHeader back={{ to: "/", label: "Return to cart" }} />);
    expect(screen.queryByRole("navigation", { name: "Main" })).toBeNull();
    const back = screen.getByRole("link", { name: "Return to cart" });
    expect(back.getAttribute("href")).toBe("/");
    expect(screen.getByRole("link", { name: "OBSERVA" })).toBeTruthy();
  });
});
