// Fixtures for stack-mode tests (*.stack.spec.ts): the real storefront and API of a running
// preview project, without mocks. Run with E2E_STACK_URL=http://127.0.0.1:18000.
// - Every browser request must stay on an allowed origin, the preview Gateway's by default (an
//   operator test sets `allowedOrigins` to the controller's); anything else is aborted and fails
//   the test at teardown, as in mocked mode. Targets are checked by stack-safety.ts: loopback
//   http only, never the live ports, the controller never on the Gateway's origin.
// - `faults`: the preview controller only (E2E_STACK_CONTROLLER_URL, default port 18090; the
//   live ports are refused). A test that starts a fault stops it in teardown, pass or fail.
// - `orders()`: what the real Gateway lists for this browser's anonymous customer.
// Each test gets a fresh browser context, so a new anonymous customer ID and empty storage.
// - E2E_STACK_BUNDLE=local serves the store's page and files from web/dist/store (after `npm run
//   build`) instead of the preview image, and every API call still reaches the real Gateway. It
//   checks a UI-only change against the real backend without building an image.
import { existsSync } from "node:fs";
import { join } from "node:path";
import { test as base, expect, request as http, type APIRequestContext, type Page } from "@playwright/test";
import type { components } from "../../src/api/generated/gateway.ts";
import { STORE_PAGE } from "./harness.ts";
import { allowedOrigin, stackTargets } from "./stack-safety.ts";

export { expect };
export type CheckoutResult = components["schemas"]["CheckoutResult"];

const TARGETS = stackTargets(process.env);
export const STACK_ORIGIN = TARGETS?.gateway ?? "";
/** The preview controller's origin (no trailing slash). */
export const CONTROLLER_URL = TARGETS?.controller ?? "";

const LOCAL_BUNDLE = process.env.E2E_STACK_BUNDLE === "local";
const DIST = join(import.meta.dirname, "..", "..", "dist", "store");
const CONTENT_TYPES: Record<string, string> = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8" };

type FaultStatus = { state: string; active: { scenario: string } | null; controls: { activate: { enabled: boolean; reason: string | null } } };

/** The preview controller: start one fault, always stopped again by the fixture. */
export class Faults {
  private started: string[] = [];
  constructor(private readonly api: APIRequestContext) {}

  async status(): Promise<FaultStatus> {
    const response = await this.api.get("/faults/status");
    expect(response.status()).toBe(200);
    return (await response.json()) as FaultStatus;
  }

  /** Waits until the controller accepts a new fault (idle: no fault, recovery finished). */
  async waitUntilIdle(timeout = 240_000) {
    await expect
      .poll(async () => (await this.status()).controls.activate.enabled, { timeout, intervals: [2_000] })
      .toBe(true);
  }

  async activate(scenario: string, parameters: Record<string, number>, duration_seconds = 120) {
    await this.waitUntilIdle();
    this.started.push(scenario);
    const response = await this.api.post("/faults/activate", { data: { scenario, duration_seconds, parameters } });
    expect(response.status(), await response.text()).toBe(200);
    expect((await this.status()).active?.scenario).toBe(scenario);
  }

  async deactivate(scenario: string) {
    const response = await this.api.post("/faults/deactivate", { data: { scenario } });
    // 200 also when nothing is active any more (already stopped or expired).
    expect(response.status(), await response.text()).toBe(200);
    this.started = this.started.filter((name) => name !== scenario);
  }

  async stopAll() {
    for (const scenario of [...this.started]) await this.deactivate(scenario);
    expect((await this.status()).active).toBeNull();
  }
}

type StackFixtures = {
  /** Origins the browser may reach in this test: the Gateway's unless a test says otherwise. */
  allowedOrigins: string[];
  offOrigin: string[];
  localBundle: boolean;
  faults: Faults;
  gateway: APIRequestContext;
  orders: (page: Page) => Promise<CheckoutResult[]>;
};

export const test = base.extend<StackFixtures>({
  allowedOrigins: [[STACK_ORIGIN], { option: true }],
  offOrigin: [
    async ({ context, allowedOrigins }, use) => {
      if (!TARGETS) throw new Error("Stack tests need E2E_STACK_URL (a preview project, never the live stack)");
      const unknown = allowedOrigins.filter((origin) => origin !== TARGETS.gateway && origin !== TARGETS.controller);
      if (unknown.length > 0) throw new Error(`allowedOrigins may only name the preview project's origins, not ${unknown.join(", ")}`);
      const seen: string[] = [];
      await context.route(
        () => true,
        (route) => {
          const url = route.request().url();
          if (allowedOrigin(url, allowedOrigins)) return route.fallback();
          seen.push(url);
          return route.abort("blockedbyclient");
        },
      );
      // The storefront opens no WebSocket; one would be a stray connection, so it is never made.
      await context.routeWebSocket(/.*/, (socket) => {
        seen.push(`websocket ${socket.url()}`);
        return socket.close();
      });
      await use(seen);
      expect(seen, "browser requests left the allowed preview origins").toEqual([]);
    },
    { auto: true },
  ],
  localBundle: [
    async ({ context }, use) => {
      if (LOCAL_BUNDLE) {
        if (!existsSync(join(DIST, "index.html"))) throw new Error("E2E_STACK_BUNDLE=local needs `npm run build` first");
        await context.route(
          (url) => url.origin === STACK_ORIGIN,
          async (route) => {
            const request = route.request();
            const { pathname } = new URL(request.url());
            const file = /^\/ui\/next\/(?:assets|licenses)\/[^/]+$/.test(pathname) ? join(DIST, pathname.slice("/ui/next/".length)) : STORE_PAGE.test(pathname) ? join(DIST, "index.html") : null;
            if (request.method() !== "GET" || !file || !existsSync(file)) return route.fallback();
            const type = CONTENT_TYPES[file.slice(file.lastIndexOf("."))] ?? "application/octet-stream";
            return route.fulfill({ path: file, contentType: type, headers: { "cache-control": "no-cache" } });
          },
        );
      }
      await use(LOCAL_BUNDLE);
    },
    { auto: true },
  ],
  faults: async ({}, use) => {
    if (!TARGETS) throw new Error("Fault tests need E2E_STACK_URL (a preview project, never the live stack)");
    const api = await http.newContext({ baseURL: CONTROLLER_URL, extraHTTPHeaders: { "x-control-token": "local-control-only" } });
    const faults = new Faults(api);
    try {
      await use(faults);
    } finally {
      try {
        await faults.stopAll();
      } finally {
        await api.dispose();
      }
    }
  },
  gateway: async ({}, use) => {
    const api = await http.newContext({ baseURL: STACK_ORIGIN });
    await use(api);
    await api.dispose();
  },
  orders: async ({ gateway }, use) => {
    await use(async (page) => {
      const customer = await customerId(page);
      const response = await gateway.get("/api/orders", { params: { customer_id: customer, limit: 50 } });
      expect(response.status()).toBe(200);
      return (await response.json()) as CheckoutResult[];
    });
  },
});

export const customerId = (page: Page) =>
  page.evaluate(() => localStorage.getItem("observa.customer") ?? "").then((id) => {
    expect(id).toMatch(/^web-[0-9a-z]{22}$/);
    return id;
  });
