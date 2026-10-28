// Fixtures for stack-mode tests (*.stack.spec.ts): the real storefront and API of a running
// preview project, without mocks. Run with E2E_STACK_URL=http://127.0.0.1:18000.
// - Every browser request must stay on the preview Gateway's origin; anything else is aborted and
//   fails the test at teardown, as in mocked mode.
// - `faults`: the preview controller only (E2E_STACK_CONTROLLER_URL, default port 18090; the
//   live ports are refused). A test that starts a fault stops it in teardown, pass or fail.
// - `orders()`: what the real Gateway lists for this browser's anonymous customer.
// Each test gets a fresh browser context, so a new anonymous customer ID and empty storage.
import { test as base, expect, request as http, type APIRequestContext, type Page } from "@playwright/test";
import type { components } from "../../src/api/generated/gateway.ts";
import { refuseLive } from "../../preview-guard.ts";

export { expect };
export type CheckoutResult = components["schemas"]["CheckoutResult"];

const STACK_URL = process.env.E2E_STACK_URL ? refuseLive("E2E_STACK_URL", process.env.E2E_STACK_URL) : "";
export const STACK_ORIGIN = STACK_URL ? new URL(STACK_URL).origin : "";
const controllerDefault = STACK_URL ? `http://${new URL(STACK_URL).hostname}:18090` : "";
export const CONTROLLER_URL = refuseLive(
  "E2E_STACK_CONTROLLER_URL",
  process.env.E2E_STACK_CONTROLLER_URL ?? (controllerDefault || "http://127.0.0.1:18090"),
);

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
  offOrigin: string[];
  faults: Faults;
  gateway: APIRequestContext;
  orders: (page: Page) => Promise<CheckoutResult[]>;
};

export const test = base.extend<StackFixtures>({
  offOrigin: [
    async ({ context }, use) => {
      if (!STACK_ORIGIN) throw new Error("Stack tests need E2E_STACK_URL (a preview project, never the live stack)");
      const seen: string[] = [];
      await context.route(
        () => true,
        (route) => {
          const url = route.request().url();
          if (new URL(url).origin === STACK_ORIGIN) return route.fallback();
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
      expect(seen, "browser requests left the preview Gateway's origin").toEqual([]);
    },
    { auto: true },
  ],
  faults: async ({}, use) => {
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
