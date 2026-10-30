// The real controller and isolated Node-only test control. Browser traffic stays on the controller.
import { test as base, expect, request as http, type APIRequestContext, type APIResponse, type Page, type Request, type Response, type Route } from "@playwright/test";
import type { components } from "../../src/api/generated/controller.ts";
import { parseUiConfig, type UiConfig } from "../../src/shared/ui-config.ts";
import { groundTruthLeaks, shouldInspectJson } from "./ground-truth.ts";
import { GenuinePasses } from "./genuine.ts";
import { installNetworkBoundary, networkBoundary } from "./network-boundary.ts";
import { PendingWork } from "./pending-work.ts";
import { redactNonces, redirectViolation } from "./redirects.ts";
import { reissueVerdict, scanReissuedResponse, ScannedRequests, type ReissueVerdict } from "./reissue-scan.ts";
import { reissueOptions, routeDecision } from "./route-policy.ts";
import { allowedOrigin, contractTarget } from "./stack-safety.ts";

export { expect };
export const CONTRACT = contractTarget(process.env);
// Fail closed, never silently drop pending work.
const DRAIN_MS = 10_000;

function targets() {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  return CONTRACT;
}

type ControllerStatus = components["schemas"]["ControllerStatus"];
type Activity = components["schemas"]["Activity"];
type HealthSnapshot = components["schemas"]["HealthSnapshot"];
type StubCall = { target: "orders" | "payments"; method: string; body: unknown; at: string };
export type SentinelContact = { method: string; path: string; at: string };

export type RecordedRequest = {
  method: string;
  path: string;
  host: string | null;
  origin: string | null;
  sec_fetch_site: string | null;
  sec_fetch_mode: string | null;
  sec_fetch_dest: string | null;
  reissued: string | null;
  at: string;
};

async function accepted<T = unknown>(response: APIResponse): Promise<T> {
  expect(response.status(), await response.text()).toBe(200);
  const body: unknown = await response.json();
  expect(groundTruthLeaks(body), `ground truth in Node JSON answer from ${response.url()}`).toEqual([]);
  return body as T;
}

export class StubControl {
  constructor(private readonly api: APIRequestContext) {}

  async mode(target: "orders" | "payments" | "all", mode: "ack" | "fail" | "hold" | "stall") {
    await accepted(await this.api.post("/__test__/mode", { data: { target, mode } }));
  }

  async release() {
    await accepted(await this.api.post("/__test__/release"));
  }

  async reset() {
    await accepted(await this.api.post("/__test__/reset"));
  }

  async setHealth(states: Record<string, string>): Promise<HealthSnapshot> {
    return accepted<HealthSnapshot>(await this.api.post("/__test__/health", { data: { states } }));
  }

  async health(): Promise<HealthSnapshot> {
    return accepted<HealthSnapshot>(await this.api.get("/__test__/health"));
  }

  async calls(): Promise<StubCall[]> {
    const body = await accepted<{ calls: StubCall[] }>(await this.api.get("/__test__/calls"));
    return body.calls;
  }

  async sentinelHits(): Promise<number> {
    const body = await accepted<{ hits: number }>(await this.api.get("/__test__/sentinel"));
    return body.hits;
  }

  async sentinelContacts(): Promise<SentinelContact[]> {
    const body = await accepted<{ contacts: SentinelContact[] }>(await this.api.get("/__test__/sentinel-contacts"));
    return body.contacts;
  }

  async requests(): Promise<RecordedRequest[]> {
    const body = await accepted<{ requests: RecordedRequest[] }>(await this.api.get("/__test__/requests"));
    return body.requests;
  }

  async redirectNonce(): Promise<string> {
    const body = await accepted<{ nonce: string }>(await this.api.post("/__test__/redirect-nonce"));
    expect(body.nonce).toMatch(/^[0-9a-f]{32}$/);
    expect(body.nonce).toHaveLength(32);
    return body.nonce;
  }
}

export class ContractController {
  constructor(private readonly api: APIRequestContext) {}

  async status(): Promise<ControllerStatus> {
    return accepted<ControllerStatus>(await this.api.get("/faults/status"));
  }

  async activity(): Promise<Activity> {
    return accepted<Activity>(await this.api.get("/activity"));
  }

  async config(): Promise<UiConfig> {
    return parseUiConfig(await accepted(await this.api.get("/ui/config")));
  }

  async deactivate(scenario: string) {
    await accepted(await this.api.post("/faults/deactivate", { data: { scenario } }));
  }

  async stopCycle() {
    await accepted(await this.api.post("/faults/auto/stop"));
  }

  async stopAll() {
    await this.stopCycle();
    const active = (await this.status()).active;
    if (active) await this.deactivate(active.scenario);
  }

  async waitUntilIdle(timeout = 30_000) {
    await expect.poll(async () => (await this.status()).controls.activate?.enabled, { timeout, intervals: [100, 250, 500] }).toBe(true);
  }
}

export type IsolationHandle = {
  /** ONLY for the routed-layer redirect self-test: exempt the sentinel so the boundary cannot mask a route failure. */
  relaxBoundaryForSentinel(): Promise<void>;
  genuine(method: "GET" | "POST", url: string): void;
  inFlight(): number;
  inspections(): { nodeScanned: number; browserReads: number };
  takeViolations(): Promise<{ offOrigin: string[]; leaks: string[] }>;
};

type ContractFixtures = {
  control: StubControl;
  controller: ContractController;
  isolation: IsolationHandle;
  clean: void;
};

export const test = base.extend<ContractFixtures>({
  // Service-worker requests bypass Playwright routes and therefore cannot be audited.
  serviceWorkers: "block",
  control: async ({}, use) => {
    const api = await http.newContext({ baseURL: targets().control, maxRedirects: 0, extraHTTPHeaders: { "X-Contract-Harness": "1" } });
    try {
      await use(new StubControl(api));
    } finally {
      await api.dispose();
    }
  },
  controller: async ({}, use) => {
    // A separate Node context: controller mutations intentionally have no browser Origin header.
    const api = await http.newContext({ baseURL: targets().controller, maxRedirects: 0 });
    try {
      await use(new ContractController(api));
    } finally {
      await api.dispose();
    }
  },
  isolation: [async ({ page }, use) => {
    // Keep the page alive through inspection; its teardown must not cancel response bodies.
    const context = page.context();
    const origin = targets().controller;
    const session = await context.newCDPSession(page);
    const passes = new GenuinePasses(origin);
    const scanned = new ScannedRequests();
    const seen: string[] = [];
    const leaks: string[] = [];
    const bodies: Promise<void>[] = [];
    const pending = new PendingWork();
    let nodeScanned = 0;
    let browserReads = 0;
    let closed = false;
    let sentinelRelaxed = false;
    try {
      // Install before any route or test navigation; a failure prevents the test body from running.
      await installNetworkBoundary(session, networkBoundary(origin));
      async function handle(route: Route) {
        if (closed) return route.abort("blockedbyclient");
        const request = route.request();
        const url = request.url();
        const headers = await request.allHeaders();
        const decision = routeDecision({
          url,
          method: request.method(),
          redirectedFrom: request.redirectedFrom() !== null,
          controllerOrigin: origin,
          takeGenuine: (method, target) => passes.take(method, target),
          headers,
        });
        if (decision.action === "abort") {
          seen.push(decision.reason ? `${decision.reason}: ${url}` : url);
          return route.abort("blockedbyclient");
        }
        // Genuine requests let Chromium follow redirects itself: only the CDP boundary protects them.
        if (decision.action === "continue") return route.continue();
        let response: APIResponse;
        try {
          // The URL, method and body are the browser's own: route.fetch gets no override of any of them.
          response = await route.fetch(reissueOptions(headers));
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          seen.push(`fetch failed ${url}: ${message.slice(0, 200)}`);
          return route.abort("blockedbyclient");
        }
        const redirect = redirectViolation(url, response.status(), response.headers());
        if (redirect !== null) {
          seen.push(redirect);
          return route.abort("blockedbyclient");
        }
        let verdict: ReissueVerdict;
        try {
          const scan = await scanReissuedResponse(response);
          nodeScanned += 1;
          verdict = reissueVerdict(url, scan);
        } catch {
          leaks.push(`JSON inspection failed for ${url}: scan failed`);
          return route.abort("blockedbyclient");
        }
        if (!verdict.deliver) {
          leaks.push(...verdict.violations);
          return route.abort("blockedbyclient");
        }
        scanned.record(request, verdict);
        await route.fulfill({ response });
      }
      await context.route(() => true, (route) => pending.track(handle(route)));
      await context.routeWebSocket(/.*/, (socket) => {
        seen.push(`websocket ${socket.url()}`);
        return socket.close();
      });
      const collect = (response: Response) => {
        // Only delivered, successfully Node-scanned re-issues are exempt; genuine passes and
        // test-level page.route fulfils still need the strict browser body read.
        if (scanned.exempts(response.request())) return;
        if (!allowedOrigin(response.url(), [origin]) || !shouldInspectJson(response.status(), response.headers()["content-type"])) return;
        browserReads += 1;
        bodies.push(response.json().then((body: unknown) => {
          leaks.push(...groundTruthLeaks(body).map((path) => `${response.url()}: ${path}`));
        }).catch((error: unknown) => {
          leaks.push(`JSON inspection failed for ${response.url()}: ${String(error)}`);
        }));
      };
      const observeRequest = (request: Request) => {
        // Detection after contact is not prevention; this audit does not stop redirect hops.
        if (!allowedOrigin(request.url(), [origin])) seen.push(request.url());
      };
      const closePopup = (other: Page) => {
        if (other === page) return;
        // Only the fixture page has a CDP boundary. A popup's first request may already have escaped.
        seen.push(`popup ${other.url()}`);
        bodies.push(other.close().catch((error: unknown) => {
          seen.push(`popup close failed: ${String(error).slice(0, 200)}`);
        }));
      };
      context.on("request", observeRequest);
      context.on("response", collect);
      context.on("page", closePopup);
      const isolation: IsolationHandle = {
        async relaxBoundaryForSentinel() {
          if (sentinelRelaxed) throw new Error("The sentinel boundary exemption may only be installed once");
          sentinelRelaxed = true;
          await installNetworkBoundary(session, networkBoundary(origin, { exemptSentinel: targets().control }));
        },
        genuine(method, url) {
          passes.allow(method, url);
        },
        inFlight() {
          return pending.size;
        },
        inspections() {
          return { nodeScanned, browserReads };
        },
        async takeViolations() {
          for (;;) {
            const drained = await pending.drain(DRAIN_MS);
            if (drained.timedOut) {
              leaks.push(`request handlers still running after ${DRAIN_MS} ms: ${pending.size}`);
              break;
            }
            if (bodies.length === 0) break;
            await Promise.all(bodies.splice(0));
          }
          return { offOrigin: seen.splice(0).map(redactNonces), leaks: leaks.splice(0).map(redactNonces) };
        },
      };
      try {
        await use(isolation);
      } finally {
        const remaining = await isolation.takeViolations();
        closed = true;
        context.off("response", collect);
        context.off("request", observeRequest);
        context.off("page", closePopup);
        expect(remaining.offOrigin, "browser requests left the contract controller origin").toEqual([]);
        expect(remaining.leaks, "ground truth must never leave the controller; every JSON response must be inspectable").toEqual([]);
        expect(passes.pending(), "a genuine Chromium request pass was never used").toBe(0);
      }
    } finally {
      await session.detach().catch(() => undefined);
    }
  }, { auto: true }],
  clean: [async ({ control, controller }, use) => {
    const reset = async () => {
      await control.reset();
      await controller.stopAll();
      await controller.waitUntilIdle();
    };
    try {
      await reset();
      await use();
    } finally {
      await reset();
      expect(await controller.status()).toMatchObject({ state: "idle", active: null });
    }
  }, { auto: true }],
});
