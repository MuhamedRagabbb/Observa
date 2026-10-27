// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://192.168.1.20:8000/ui/next/orders?x=1#top"}
// Each shell links to the other app on the same host, using the port that /ui/config
// reports, from one GET /ui/config on its own origin. (The store home also reads its
// products; that request is answered with an empty list here and not counted.)
import { cleanup, render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, type RouteObject } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { operatorRoutes } from "../src/operator/routes.tsx";
import { AppProviders } from "../src/shared/AppProviders.tsx";
import { crossAppUrl, type UiConfig } from "../src/shared/ui-config.ts";
import { storeRoutes } from "../src/store/routes.tsx";

const config = (mode: UiConfig["mode"]): UiConfig => ({
  mode,
  ports: { store: 18000, control: 18090, grafana: 3000, jaeger: 16686, prometheus: 9090 },
});

const urlOf = (input: RequestInfo | URL) => (input instanceof Request ? input.url : String(input));

/** Answers /ui/config with `respond`; the store's product request gets an empty list. */
function stubFetch(respond: () => Promise<Response>) {
  const configCalls: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = urlOf(input);
      if (url.endsWith("/api/products")) return Promise.resolve(Response.json([]));
      configCalls.push(url);
      return respond();
    }),
  );
  return configCalls;
}

function renderShell(routes: RouteObject[]) {
  const router = createMemoryRouter(routes, { initialEntries: ["/"] });
  render(
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("crossAppUrl", () => {
  it("keeps the page's scheme and host and swaps in the target app's port", () => {
    const page = new URL("http://192.168.1.20:8000/ui/next/orders?x=1#top");
    expect(crossAppUrl(config("store"), "control", page)).toBe("http://192.168.1.20:18090/ui/next/");
    expect(crossAppUrl(config("control"), "store", new URL("https://demo.test:18090/ui/next/"))).toBe(
      "https://demo.test:18000/",
    );
    expect(crossAppUrl(config("store"), "control", new URL("http://[::1]:8000/ui/next/"))).toBe(
      "http://[::1]:18090/ui/next/",
    );
  });
});

describe("cross-app links", () => {
  it("the storefront links to Experiments on the controller's port", async () => {
    const configCalls = stubFetch(async () => Response.json(config("store")));
    renderShell(storeRoutes);
    const link = await screen.findByRole("link", { name: "Experiments" });
    expect(link.getAttribute("href")).toBe("http://192.168.1.20:18090/ui/next/");
    expect(configCalls).toEqual(["/ui/config"]);
  });

  it("the operator links back to the Storefront on the Gateway's port", async () => {
    stubFetch(async () => Response.json(config("control")));
    renderShell(operatorRoutes);
    const link = await screen.findByRole("link", { name: "Storefront" });
    // The storefront is at / on the Gateway since P4-H.
    expect(link.getAttribute("href")).toBe("http://192.168.1.20:18000/");
  });

  it("shows no link rather than a guessed one when /ui/config fails", async () => {
    const configCalls = stubFetch(async () => new Response("down", { status: 503 }));
    renderShell(storeRoutes);
    await vi.waitFor(() => expect(configCalls).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole("link", { name: "Experiments" })).toBeNull();
    expect(screen.getByRole("main", { name: "Storefront" })).toBeTruthy();
  });

  it("shows no link when /ui/config is malformed", async () => {
    const configCalls = stubFetch(async () => Response.json({ mode: "store", ports: { control: "8090" } }));
    renderShell(storeRoutes);
    await vi.waitFor(() => expect(configCalls).toHaveLength(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole("link", { name: "Experiments" })).toBeNull();
  });
});
