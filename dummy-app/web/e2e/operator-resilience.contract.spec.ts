import { readFileSync } from "node:fs";
import { request as http, type Page, type Route } from "@playwright/test";
import type { components } from "../src/api/generated/controller.ts";
import { CONTRACT, expect, test, type ContractController, type StubControl } from "./support/contract.ts";
import { activityEvents, banner, expandAll, field, inject, manual, openOperator, title } from "./support/contract-operator.ts";
import { groundTruthLeaks } from "./support/ground-truth.ts";

const WAIT = { timeout: 10_000 };
type ControllerStatus = components["schemas"]["ControllerStatus"];
const payment = { scenario: "payments_errors", duration_seconds: 30, parameters: { failure_rate: 0.9, status_code: 503 } };
const stop = (page: Page) => banner(page).getByRole("button", { name: "Stop fault", exact: true });
// Wait in real time beyond STALE_AFTER_SECONDS (7.5 s) while hidden-tab polling is paused.
const STALE_WAIT_MS = 8_500;
// What a window-level listener (the one TanStack Query's focusManager registers) must see, in order.
const RETURNED = { overridden: false, state: "visible", windowSaw: ["hidden", "visible"] };

/**
 * A hidden tab: the page's polling pauses (refetchIntervalInBackground is false). The event bubbles
 * because TanStack Query's focusManager listens on `window`, not on `document`. A probe on `window`
 * records each event it receives and the visibility it saw, so event DELIVERY is observed directly
 * (deterministically, not through request timing, which a poll tick or an in-flight read can confound).
 */
async function hideTab(page: Page) {
  await page.evaluate(() => {
    const probe: string[] = [];
    (window as Window & { __visibilityProbe?: string[] }).__visibilityProbe = probe;
    window.addEventListener("visibilitychange", () => probe.push(document.visibilityState));
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
  });
}

/**
 * Back to the tab: drop the temporary own-property override so the browser's real getter shows again,
 * notify window-level listeners, and report what happened.
 */
async function showTab(page: Page) {
  return page.evaluate(() => {
    Reflect.deleteProperty(document, "visibilityState");
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }));
    return {
      overridden: Object.getOwnPropertyDescriptor(document, "visibilityState") !== undefined,
      state: document.visibilityState,
      // Consecutive repeats are one transition: a native notification during the hidden interval would read the
      // overridden value again. Order and the values themselves stay strict.
      windowSaw: ((window as Window & { __visibilityProbe?: string[] }).__visibilityProbe ?? []).filter((value, index, all) => index === 0 || value !== all[index - 1]),
    };
  });
}

/**
 * Runs `body` with the tab hidden, then returns to it. The restoration always runs. A failure of the body
 * is never replaced by a restoration failure; when the body succeeded, a restoration failure or a wrong
 * restored state fails the test.
 */
async function whileTabHidden(page: Page, body: () => Promise<void>) {
  await hideTab(page);
  let restored: Awaited<ReturnType<typeof showTab>> | undefined;
  let restoreFailure: unknown;
  try {
    await body();
  } finally {
    restored = await showTab(page).catch((error: unknown) => {
      restoreFailure = error;
      return undefined;
    });
  }
  if (restoreFailure !== undefined) throw restoreFailure;
  expect(restored, "window-level listeners saw hidden then visible, and the real visibility is back").toEqual(RETURNED);
}

function origin() {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  return CONTRACT.controller;
}

async function mark(controller: ContractController, control: StubControl) {
  return { since: (await activityEvents(controller))[0]?.at ?? "", calls: (await control.calls()).length, requests: (await control.requests()).length };
}

async function browserPost(page: Page, path: string, body?: unknown) {
  return page.evaluate(async ({ path, body }) => {
    const answer = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: answer.status, body: await answer.json() as ControllerStatus };
  }, { path, body });
}

async function startPayment(page: Page) {
  await openOperator(page, "Idle");
  await expandAll(page);
  await field(page, "Duration").fill("30");
  await inject(page).click();
  await expect(manual(page)).toContainText("Controller confirmed the fault is active.", WAIT);
  await expect(title(page)).toHaveText("Payment Errors", WAIT);
}

async function idle(page: Page, controller: ContractController) {
  await expect.poll(async () => (await controller.status()).state, WAIT).toBe("idle");
  await expect(title(page)).toHaveText("Idle", WAIT);
}

test("row 12: a stale active answer still permits one confirmed Stop fault", async ({ page, control, controller }) => {
  test.setTimeout(55_000);
  const checkpoint = await mark(controller, control);
  await startPayment(page);
  await whileTabHidden(page, async () => {
    await page.waitForTimeout(STALE_WAIT_MS);
    await expect(manual(page)).toContainText("Waiting for a fresh answer from the controller.", WAIT);
    await expect(page.getByRole("banner").getByText("Controller connected", { exact: true })).toBeVisible(WAIT);
    await expect(stop(page)).not.toHaveAttribute("aria-disabled", "true");
    await stop(page).click();
    await expect(page.getByText("Controller confirmed the fault stopped.", { exact: true })).toBeVisible(WAIT);
    expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "stop_requested")).toHaveLength(1);
    expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST" && request.path === "/faults/deactivate")).toHaveLength(1);
    expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(["PUT", "DELETE"]);
  });
  await idle(page, controller);
});

test("row 12: Inject on a stale idle answer only refreshes with GET and sends no POST", async ({ page, control, controller }) => {
  test.setTimeout(55_000);
  const checkpoint = await mark(controller, control);
  await openOperator(page, "Idle");
  await expandAll(page);
  await whileTabHidden(page, async () => {
    await page.waitForTimeout(STALE_WAIT_MS);
    await expect(manual(page)).toContainText("Waiting for a fresh answer from the controller.", WAIT);
    await expect(inject(page)).toHaveAttribute("aria-disabled", "true", WAIT);
    const beforePress = (await control.requests()).length;
    const read = page.waitForRequest((request) => request.method() === "GET" && request.url() === `${origin()}/faults/status`, WAIT);
    // aria-disabled preserves the UI's explicit press-to-refresh behavior.
    await inject(page).click({ force: true });
    await read;
    await expect(manual(page)).toContainText("The controller’s state is being re-read. Try again in a moment.", WAIT);
    // Judged BEFORE returning to the tab, so a refresh caused by the return can never satisfy it. The
    // controller records a request just after the browser starts it, hence the bounded poll.
    await expect.poll(async () => (await control.requests()).slice(beforePress).some((request) => request.method === "GET" && request.path === "/faults/status"), WAIT).toBe(true);
    expect((await control.requests()).slice(beforePress).filter((request) => request.method === "POST")).toHaveLength(0);
  });
  expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "fault_requested")).toHaveLength(0);
  expect((await control.calls()).slice(checkpoint.calls)).toEqual([]);
  await idle(page, controller);
});

test("row 13: disconnected status shows Retry connection and reconnects to the live state", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  await startPayment(page);
  const url = `${origin()}/faults/status`;
  const abort = (route: Route) => route.request().method() === "GET" ? route.abort("failed") : route.fallback();
  await page.route(url, abort);
  try {
    await expect(page.getByRole("banner").getByText("Controller disconnected", { exact: true })).toBeVisible(WAIT);
    await expect(title(page)).toHaveText("Unreachable", WAIT);
    await expect(banner(page).getByRole("button", { name: "Retry connection", exact: true })).toBeVisible(WAIT);
    expect(await controller.status()).toMatchObject({ state: "manual", active: { scenario: "payments_errors" } });
  } finally {
    await page.unroute(url, abort);
  }
  const read = page.waitForResponse((response) => response.request().method() === "GET" && response.url() === url, WAIT);
  await banner(page).getByRole("button", { name: "Retry connection", exact: true }).click();
  expect((await read).status()).toBe(200);
  await expect(page.getByRole("banner").getByText("Controller connected", { exact: true })).toBeVisible(WAIT);
  await expect(title(page)).toHaveText("Payment Errors", WAIT);
  expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "fault_requested")).toHaveLength(1);
  expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST").map((request) => request.path)).toEqual(["/faults/activate"]);
  expect((await control.calls()).slice(checkpoint.calls)).toMatchObject([{ target: "payments", method: "PUT" }]);
});

// A real cross-origin BROWSER page is out of scope: the isolation boundary confines
// the browser to one origin. Gate A's genuine-pass test already proves the page's
// own POST passes with same-origin headers. These are NEW, independent Node contexts.
const guardCases = [
  { name: "localhost Origin alias", code: "origin_not_allowed", headers: (controller: string) => ({ Origin: `http://localhost:${new URL(controller).port}` }) },
  { name: "foreign Origin", code: "origin_not_allowed", headers: () => ({ Origin: "http://evil.example" }) },
  { name: "cross-site Sec-Fetch-Site", code: "cross_site", headers: (controller: string) => ({ Origin: controller, "Sec-Fetch-Site": "cross-site" }) },
  // APIRequestContext permits a Host override; no browser forbidden-header API is used.
  { name: "foreign Host", code: "host_not_allowed", headers: () => ({ Host: "evil.example" }) },
];

for (const item of guardCases) {
  test(`row 14: Node rejects ${item.name} without an incident or stub call`, async ({ control, controller }) => {
    const checkpoint = await mark(controller, control);
    const api = await http.newContext({ baseURL: origin(), maxRedirects: 0 });
    try {
      const headers = item.headers(origin());
      const response = await api.post("/faults/activate", { data: payment, headers, maxRedirects: 0 });
      expect(response.status()).toBe(403);
      const body: unknown = await response.json();
      expect(body).toMatchObject({ error_code: item.code });
      expect(groundTruthLeaks(body)).toEqual([]);
      expect(await controller.status()).toMatchObject({ state: "idle", active: null, auto_running: false, auto: null });
      expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "fault_requested")).toHaveLength(0);
      expect((await control.calls()).slice(checkpoint.calls)).toEqual([]);
      const posts = (await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST");
      expect(posts).toHaveLength(1);
      expect(posts[0]).toMatchObject({
        path: "/faults/activate",
        host: "Host" in headers ? headers.Host : new URL(origin()).host,
        origin: "Origin" in headers ? headers.Origin : null,
        sec_fetch_site: "Sec-Fetch-Site" in headers ? headers["Sec-Fetch-Site"] : null,
      });
    } finally {
      await api.dispose();
    }
  });
}

test("row 15: manual and cycle incidents keep ground truth only in the journal", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  await openOperator(page, "Idle");
  const api = await http.newContext({ baseURL: origin(), maxRedirects: 0 });
  const inspectReads = async () => {
    for (const path of ["/faults/status", "/activity", "/services/health"]) {
      const response = await api.get(path);
      expect(response.status()).toBe(200);
      expect(groundTruthLeaks(await response.json()), `ground truth in Node GET ${path}`).toEqual([]);
    }
  };
  try {
    const started = await browserPost(page, "/faults/activate", payment);
    expect(started.status).toBe(200);
    expect(groundTruthLeaks(started.body)).toEqual([]);
    expect(started.body).toMatchObject({ state: "manual", active: { scenario: "payments_errors", status: "active" } });
    const manualId = started.body.active?.incident_id;
    expect(manualId).toEqual(expect.any(String));
    await inspectReads();
    const stopped = await browserPost(page, "/faults/deactivate", { scenario: payment.scenario });
    expect(stopped.status).toBe(200);
    expect(groundTruthLeaks(stopped.body)).toEqual([]);
    expect(stopped.body.active).toBeNull();
    await idle(page, controller);
    await inspectReads();

    // Row 15 needs time to read the live incident ID and all three Node APIs. This cycle is posted directly
    // from the browser, so it needs no UI form values (row 9 drives the real form with observable 0/6/6 s values).
    const cycle = { seed: 7341, scenarios: ["bad_deployment"], shuffle: false, warmup_seconds: 0, duration_seconds: 3, recovery_seconds: 0, repeat: 1 };
    const automatic = await browserPost(page, "/faults/auto/start", cycle);
    expect(automatic.status).toBe(200);
    expect(groundTruthLeaks(automatic.body)).toEqual([]);
    expect(automatic.body.auto?.config).toEqual(cycle);
    let cycleId: string | undefined;
    await expect.poll(async () => {
      const status = await controller.status();
      cycleId = status.active?.incident_id;
      return status.active?.scenario;
    }, { ...WAIT, intervals: [100, 250, 500] }).toBe("bad_deployment");
    expect(cycleId).toEqual(expect.any(String));
    await inspectReads();
    await expect.poll(async () => (await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "cycle_finished").length, WAIT).toBe(1);
    await idle(page, controller);
    await inspectReads();

    // Read only. Match THIS test's incident IDs, never a prior run's journal rows.
    const journal = readFileSync(new URL("../e2e-output/contract-journal/incidents.jsonl", import.meta.url), "utf8")
      .split(/\r?\n/).filter((line) => line.trim()).map((line) => JSON.parse(line) as Record<string, unknown>);
    const incidents = journal.filter((record) => record.incident_id === manualId || record.incident_id === cycleId);
    expect(incidents).toHaveLength(2);
    expect(incidents).toMatchObject([
      { incident_id: manualId, scenario: "payments_errors", status: "ended", root_cause_service: "payments-service", expected_signals: ["payment_failures", "orders_errors", "failed_payment_spans"] },
      { incident_id: cycleId, scenario: "bad_deployment", status: "ended", root_cause_service: "orders-service", expected_signals: ["release_changed", "slow_order_persistence", "OrderPersistenceCompatibilityError"] },
    ]);
    for (const record of incidents) expect(groundTruthLeaks(record)).toEqual(expect.arrayContaining(["root_cause_service", "expected_signals"]));
    const events = await activityEvents(controller, checkpoint.since);
    expect(events.filter((event) => event.event === "fault_requested")).toHaveLength(2);
    expect(events.filter((event) => event.event === "stop_requested")).toHaveLength(2);
    expect(events.filter((event) => event.event === "cycle_started")).toHaveLength(1);
    expect(events.filter((event) => event.event === "cycle_finished")).toHaveLength(1);
    expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST").map((request) => request.path)).toEqual([
      "/faults/activate", "/faults/deactivate", "/faults/auto/start",
    ]);
    expect((await control.calls()).slice(checkpoint.calls).map((call) => ({ target: call.target, method: call.method }))).toEqual([
      { target: "payments", method: "PUT" }, { target: "payments", method: "DELETE" }, { target: "orders", method: "PUT" }, { target: "orders", method: "DELETE" },
    ]);
  } finally {
    await api.dispose();
  }
});

test("row 16: Service health shows each stub state exactly and then returns to healthy", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  await openOperator(page, "Idle");
  // These are the four real HEALTH_SERVICES, not synthetic row names.
  const states = { "api-gateway": "degraded", "orders-service": "unavailable", "inventory-service": "unknown", "payments-service": "healthy" };
  await control.setHealth(states);
  const region = page.getByRole("region", { name: "Service health", exact: true });
  const table = region.getByRole("table", { name: "Service health", exact: true });
  await expect(table.getByRole("row")).toHaveCount(5, WAIT);
  for (const [service, state] of Object.entries(states)) {
    const row = table.getByRole("row").filter({ has: page.getByRole("rowheader", { name: service, exact: true }) });
    await expect(row).toHaveCount(1, WAIT);
    const label = state.charAt(0).toUpperCase() + state.slice(1);
    await expect(row.getByText(label, { exact: true })).toHaveCount(1, WAIT);
    await expect(row.getByText(label, { exact: true })).toBeVisible(WAIT);
    for (const other of ["Healthy", "Degraded", "Unavailable", "Unknown"].filter((value) => value !== label)) {
      await expect(row.getByText(other, { exact: true })).toHaveCount(0);
    }
  }
  await control.setHealth(Object.fromEntries(Object.keys(states).map((service) => [service, "healthy"])));
  for (const service of Object.keys(states)) {
    const row = table.getByRole("row").filter({ has: page.getByRole("rowheader", { name: service, exact: true }) });
    await expect(row.getByText("Healthy", { exact: true })).toHaveCount(1, WAIT);
    await expect(row.getByText("Healthy", { exact: true })).toBeVisible(WAIT);
  }
  await expect(table.getByText("Healthy", { exact: true })).toHaveCount(4, WAIT);
  for (const label of ["Degraded", "Unavailable", "Unknown"]) await expect(table.getByText(label, { exact: true })).toHaveCount(0);
  expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST")).toHaveLength(0);
  expect((await control.calls()).slice(checkpoint.calls)).toEqual([]);
});

test("row 16: Session activity excludes Node actions before the visit and includes those after it", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  const api = await http.newContext({ baseURL: origin(), maxRedirects: 0 });
  const post = async (path: string, data: unknown) => {
    const response = await api.post(path, { data });
    expect(response.status()).toBe(200);
    expect(groundTruthLeaks(await response.json())).toEqual([]);
  };
  try {
    await post("/faults/activate", { scenario: "bad_deployment", duration_seconds: 30 });
    await post("/faults/deactivate", { scenario: "bad_deployment" });
    await expect.poll(async () => (await controller.status()).state, WAIT).toBe("idle");
    const beforeVisit = await mark(controller, control);
    expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "fault_requested")).toHaveLength(1);
    const firstActivity = page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname === "/activity", WAIT);
    await openOperator(page, "Idle");
    expect((await firstActivity).status()).toBe(200);
    const region = page.getByRole("region", { name: "Session activity", exact: true });
    await expect(region.getByRole("listitem").filter({ hasText: "F3 Bad Deployment" })).toHaveCount(0);

    await post("/faults/activate", payment);
    await post("/faults/deactivate", { scenario: payment.scenario });
    await expect(region.getByText(/^Fault requested.*F2 Payment Errors/)).toHaveCount(1, WAIT);
    await expect(region.getByText("Stop requested · F2 Payment Errors", { exact: true })).toBeVisible(WAIT);
    await expect(region.getByText("Fault stopped · F2 Payment Errors · stopped manually", { exact: true })).toBeVisible(WAIT);
    await expect(region.getByText("Controller confirmed fault is active", { exact: true })).toHaveCount(1, WAIT);
    await expect(region.getByRole("listitem").filter({ hasText: "F3 Bad Deployment" })).toHaveCount(0);
    const events = await activityEvents(controller, beforeVisit.since);
    expect(events.filter((event) => event.event === "fault_requested")).toHaveLength(1);
    expect(events.filter((event) => event.event === "stop_requested")).toHaveLength(1);
    expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST").map((request) => request.path)).toEqual([
      "/faults/activate", "/faults/deactivate", "/faults/activate", "/faults/deactivate",
    ]);
    expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(["PUT", "DELETE", "PUT", "DELETE"]);
    await idle(page, controller);
  } finally {
    await api.dispose();
  }
});
