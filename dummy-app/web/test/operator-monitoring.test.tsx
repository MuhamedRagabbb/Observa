// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8090/"}
import { focusManager } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activity, controllerStatus, CONTROLLER_NOW, healthSnapshot, type Activity, type ControllerStatus, type HealthSnapshot } from "../e2e/fixtures/controller.ts";
import { OPERATOR_UI_CONFIG } from "../e2e/fixtures/gateway.ts";
import { operatorRoutes } from "../src/operator/routes.tsx";
import { AppProviders } from "../src/shared/AppProviders.tsx";

type Answer<T> = T | "network-error" | "hang" | Response;
let statusAnswers: Answer<ControllerStatus>[] = [];
let healthAnswers: Answer<HealthSnapshot>[] = [];
let activityAnswers: Answer<Activity>[] = [];
let configAnswers: Answer<typeof OPERATOR_UI_CONFIG>[] = [];
const calls: { method: string; path: string; search: string }[] = [];

function next<T>(answers: Answer<T>[]): Answer<T> {
  return answers.length > 1 ? answers.shift() as Answer<T> : answers[0] as Answer<T>;
}

async function respond<T>(answer: Answer<T>, request: Request): Promise<Response> {
  if (answer === "network-error") throw new TypeError("Failed to fetch");
  if (answer === "hang") return new Promise<Response>((_, reject) => request.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
  if (answer instanceof Response) return answer;
  return Response.json(answer);
}

function stub() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.href), init);
    const url = new URL(request.url);
    calls.push({ method: request.method, path: url.pathname, search: url.search });
    switch (url.pathname) {
      case "/faults/status": return respond(next(statusAnswers), request);
      case "/services/health": return respond(next(healthAnswers), request);
      case "/activity": return respond(next(activityAnswers), request);
      case "/ui/config": return respond(next(configAnswers), request);
      default: return new Response("not in this test", { status: 404 });
    }
  }));
}

const flush = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    for (let turn = 0; turn < 5; turn++) await vi.advanceTimersByTimeAsync(1);
  });
};

async function open() {
  render(<AppProviders><RouterProvider router={createMemoryRouter(operatorRoutes, { initialEntries: ["/"] })} /></AppProviders>);
  await flush();
  await flush();
}

const healthTable = () => screen.getByRole("table", { name: "Service health" });
const healthRow = (service: string) => within(healthTable()).getByRole("row", { name: new RegExp(service) });
const expectGetOnly = () => {
  expect(calls.length).toBeGreaterThan(0);
  expect(new Set(calls.map(({ method }) => method))).toEqual(new Set(["GET"]));
};

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"] });
  statusAnswers = [controllerStatus("idle")];
  healthAnswers = [healthSnapshot()];
  activityAnswers = [activity([])];
  configAnswers = [OPERATOR_UI_CONFIG];
  calls.length = 0;
  stub();
  focusManager.setFocused(true);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  focusManager.setFocused(undefined);
});

describe("Operator monitoring dashboard", () => {
  it("reads status, health, activity with limit=200 and UI config using GET only", async () => {
    await open();
    expect(calls.some((call) => call.path === "/faults/status")).toBe(true);
    expect(calls.some((call) => call.path === "/services/health")).toBe(true);
    expect(calls.some((call) => call.path === "/activity" && call.search === "?limit=200")).toBe(true);
    expect(calls.some((call) => call.path === "/ui/config")).toBe(true);
    expectGetOnly();
  });

  it("shows all four healthy services and their check times", async () => {
    await open();
    for (const service of ["api-gateway", "orders-service", "inventory-service", "payments-service"]) {
      const row = healthRow(service);
      expect(within(row).getByText("Healthy")).toBeTruthy();
      expect(row.textContent).toMatch(/\d\d:\d\d:\d\d/);
    }
    expectGetOnly();
  });

  it("shows a degraded service and its reasons in the same row", async () => {
    healthAnswers = [healthSnapshot({ "payments-service": { state: "degraded", reasons: ["high_error_ratio", "high_latency"] } })];
    await open();
    const row = healthRow("payments-service");
    expect(within(row).getByText("Degraded")).toBeTruthy();
    expect(row.textContent).toContain("High error ratio · High latency");
    expectGetOnly();
  });

  it("explains that metrics are unavailable", async () => {
    healthAnswers = [healthSnapshot({}, "unavailable")];
    await open();
    await flush(60); // a live notice adds its text a moment after it mounts
    expect(screen.getByText("Metrics are unavailable, so only services the controller can check directly are judged.")).toBeTruthy();
    expectGetOnly();
  });

  it("polls service health and activity again every five seconds", async () => {
    await open();
    const healthBefore = calls.filter((call) => call.path === "/services/health").length;
    const activityBefore = calls.filter((call) => call.path === "/activity").length;
    await flush(5000);
    expect(calls.filter((call) => call.path === "/services/health").length).toBe(healthBefore + 1);
    expect(calls.filter((call) => call.path === "/activity").length).toBe(activityBefore + 1);
    expectGetOnly();
  });

  it("marks every service unknown after a health network error and restores the real states", async () => {
    healthAnswers = [healthSnapshot(), "network-error", healthSnapshot({ "payments-service": { state: "degraded", reasons: ["high_latency"] } })];
    await open();
    await flush(5000);
    for (const service of ["api-gateway", "orders-service", "inventory-service", "payments-service"]) {
      const row = healthRow(service);
      expect(within(row).getByText("Unknown")).toBeTruthy();
      expect(row.textContent).toContain("—");
      expect(row.textContent).toContain("Controller not reachable");
    }
    await flush(60);
    expect(screen.getByText("Can’t reach the controller, so service health is unknown.")).toBeTruthy();
    await flush(5000);
    expect(within(healthRow("api-gateway")).getByText("Healthy")).toBeTruthy();
    expect(within(healthRow("payments-service")).getByText("Degraded")).toBeTruthy();
    expectGetOnly();
  });

  it("marks health unknown when the controller status header disconnects", async () => {
    statusAnswers = [controllerStatus("idle"), "network-error"];
    await open();
    await flush(2500);
    await flush();
    expect(screen.getByText("Controller disconnected")).toBeTruthy();
    for (const service of ["api-gateway", "orders-service", "inventory-service", "payments-service"]) {
      expect(within(healthRow(service)).getByText("Unknown")).toBeTruthy();
    }
    expectGetOnly();
  });

  it("shows the four configured observability links only after UI config answers", async () => {
    configAnswers = ["hang"];
    await open();
    expect(screen.queryByRole("list", { name: "Observability tools" })).toBeNull();
    configAnswers = [OPERATOR_UI_CONFIG];
    // The query stays pending for this test by design, so link visibility is verified in a separate mount.
    cleanup();
    vi.unstubAllGlobals();
    calls.length = 0;
    configAnswers = [OPERATOR_UI_CONFIG];
    stub();
    await open();
    const list = screen.getByRole("list", { name: "Observability tools" });
    const links = within(list).getAllByRole("link");
    expect(links).toHaveLength(4);
    for (const [name, caption, href] of [
      ["Grafana", "Dashboards", "http://127.0.0.1:13000/"],
      ["Prometheus", "Metrics", "http://127.0.0.1:19090/"],
      ["Loki", "Logs", "http://127.0.0.1:13000/explore"],
      ["Jaeger", "Traces", "http://127.0.0.1:26686/"],
    ]) {
      const link = within(list).getByRole("link", { name: new RegExp(`${name}.*${caption}`) });
      expect(link.getAttribute("href")).toBe(href);
    }
    expectGetOnly();
  });

  it("lists only activity at or after this session start, newest first", async () => {
    const newer = { at: new Date((CONTROLLER_NOW + 5) * 1000).toISOString(), event: "fault_active", scenario: "payments_errors" };
    const earlier = { at: new Date((CONTROLLER_NOW - 1) * 1000).toISOString(), event: "fault_requested", scenario: "payments_errors" };
    statusAnswers = [controllerStatus("idle", { now: CONTROLLER_NOW })];
    activityAnswers = [activity([earlier, newer])];
    await open();
    const list = screen.getByRole("list", { name: "Session activity log" });
    expect(within(list).getByText("Controller confirmed fault is active")).toBeTruthy();
    expect(list.textContent).not.toContain("Fault requested");
    expect(list.querySelector("li")?.textContent).toContain("Controller confirmed fault is active");
    expectGetOnly();
  });

  it("shows the empty session activity message when there are no entries", async () => {
    await open();
    expect(screen.getByText("No activity in this session yet.")).toBeTruthy();
    expectGetOnly();
  });

  it("lists the same entry only once across consecutive activity polls", async () => {
    const entry = { at: new Date((CONTROLLER_NOW + 5) * 1000).toISOString(), event: "fault_active", scenario: "payments_errors" };
    statusAnswers = [controllerStatus("idle", { now: CONTROLLER_NOW })];
    activityAnswers = [activity([entry]), activity([entry])];
    await open();
    await flush(5000);
    const list = screen.getByRole("list", { name: "Session activity log" });
    expect(within(list).getAllByText("Controller confirmed fault is active")).toHaveLength(1);
    expectGetOnly();
  });

  it("keeps previously listed activity and shows the stale log notice on failure", async () => {
    const entry = { at: new Date((CONTROLLER_NOW + 5) * 1000).toISOString(), event: "fault_active", scenario: "payments_errors" };
    statusAnswers = [controllerStatus("idle", { now: CONTROLLER_NOW })];
    activityAnswers = [activity([entry]), "network-error"];
    await open();
    await flush(5000);
    await flush(60);
    expect(screen.getByText("Can’t read the controller’s activity log. The list below may be out of date.")).toBeTruthy();
    expect(screen.getByText("Controller confirmed fault is active")).toBeTruthy();
    expectGetOnly();
  });

  it("shows planned enhancements as unavailable reference text without controls", async () => {
    await open();
    const region = screen.getByRole("region", { name: "Planned enhancements" });
    for (const item of [
      "Severity presets with editable parameters",
      "Affected-order rate for F3 Bad Deployment",
      "Per-scenario parameters inside the automatic cycle",
      "Traffic controls with safe operating limits",
    ]) expect(within(region).getByText(item)).toBeTruthy();
    expect(within(region).getByText("Not available yet")).toBeTruthy();
    for (const role of ["button", "link", "textbox", "checkbox"] as const) expect(within(region).queryByRole(role)).toBeNull();
    expectGetOnly();
  });

  it("exposes named section regions, health column headers and the four-link tool list", async () => {
    await open();
    for (const name of ["Experiment", "Service health", "Session activity", "Planned enhancements"]) {
      const region = screen.getByRole("region", { name });
      if (name === "Experiment") expect(region.querySelector("h1")).toBeTruthy();
      else expect(within(region).getByRole("heading", { name })).toBeTruthy();
    }
    const table = healthTable();
    for (const name of ["Service", "Status", "Last check"]) expect(within(table).getByRole("columnheader", { name })).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Observability tools" })).getAllByRole("link")).toHaveLength(4);
    expectGetOnly();
  });
});
