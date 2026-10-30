// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8090/ui/next/"}
import { focusManager, onlineManager } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { controllerStatus, type ControllerStatus } from "../e2e/fixtures/controller.ts";
import { OPERATOR_UI_CONFIG } from "../e2e/fixtures/gateway.ts";
import { operatorRoutes } from "../src/operator/routes.tsx";
import { postStart } from "../src/operator/manual/fault-actions.ts";
import { resetFaultActionCooldown } from "../src/operator/manual/use-fault-actions.ts";
import { AppProviders } from "../src/shared/AppProviders.tsx";

type Answer<T> = T | "network-error" | "hang" | Response;
type Reply = { status: number; body: unknown } | "network-error" | "hang";
type Call = { method: string; path: string; body?: unknown; headers: Headers };
let statuses: Answer<ControllerStatus>[] = [];
let activate: Reply[] = [];
let deactivate: Reply[] = [];
const calls: Call[] = [];
let held: (() => void) | null = null;

function next<T>(answers: Answer<T>[]): Answer<T> {
  return answers.length > 1 ? answers.shift() as Answer<T> : answers[0] as Answer<T>;
}

async function reply<T>(answer: Answer<T>, request: Request): Promise<Response> {
  if (answer === "network-error") throw new TypeError("Failed to fetch");
  if (answer === "hang") return new Promise<Response>((resolve, reject) => { request.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))); held = () => resolve(Response.json(controllerStatus("manual"))); });
  if (answer instanceof Response) return answer;
  return Response.json(answer);
}

function actionReply(answer: Reply, request: Request): Promise<Response> | Response {
  if (answer === "network-error") return Promise.reject(new TypeError("Failed to fetch"));
  if (answer === "hang") return new Promise<Response>((resolve, reject) => { request.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))); held = () => resolve(Response.json(controllerStatus("manual"))); });
  return Response.json(answer.body, { status: answer.status });
}

function stub() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.href), init);
    const url = new URL(request.url);
    let body: unknown;
    if (request.method === "POST") {
      const raw = await request.clone().text();
      body = raw ? JSON.parse(raw) : undefined;
    }
    calls.push({ method: request.method, path: url.pathname, body, headers: request.headers });
    if (request.method === "POST") {
      if (url.pathname === "/faults/activate") return actionReply(activate.length > 1 ? activate.shift()! : activate[0]!, request);
      if (url.pathname === "/faults/deactivate") return actionReply(deactivate.length > 1 ? deactivate.shift()! : deactivate[0]!, request);
      throw new Error(`Unexpected POST ${url.pathname}`);
    }
    if (url.pathname === "/ui/config") return Response.json(OPERATOR_UI_CONFIG);
    if (url.pathname === "/faults/status") return reply(next(statuses), request);
    if (url.pathname === "/services/health") return Response.json({ checked_at: new Date().toISOString(), metrics: "available", services: [] });
    if (url.pathname === "/activity" && url.search === "?limit=200") return Response.json({ capacity: 200, entries: [] });
    throw new Error(`Unexpected GET ${url.pathname}${url.search}`);
  }));
}

// 60 ms by default: a live Notice adds its text 50 ms after it mounts.
const flush = async (ms = 60) => act(async () => {
  await vi.advanceTimersByTimeAsync(ms);
  for (let turn = 0; turn < 5; turn++) await vi.advanceTimersByTimeAsync(1);
});

async function open() {
  render(<AppProviders><RouterProvider router={createMemoryRouter(operatorRoutes, { initialEntries: ["/"] })} /></AppProviders>);
  await flush(); await flush();
}

const banner = () => screen.getByRole("region", { name: "Experiment" });
const manual = () => screen.getByRole("region", { name: "Manual fault injection" });
const startButton = () => within(manual()).getByRole("button", { name: "Inject fault" });
const posts = (path: string) => calls.filter((call) => call.method === "POST" && call.path === path);
const edit = (name: string, value: string) => fireEvent.change(manual().querySelector<HTMLInputElement>(`input[name="${name}"]`)!, { target: { value } });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"] });
  statuses = [controllerStatus("idle")]; activate = [{ status: 200, body: controllerStatus("manual") }];
  deactivate = [{ status: 200, body: controllerStatus("recovering") }];
  calls.length = 0; held = null; resetFaultActionCooldown(); stub(); focusManager.setFocused(true);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); focusManager.setFocused(undefined); });

describe("manual fault controls", () => {
  it("A starts with defaults and confirms in the banner", async () => {
    await open(); statuses = [controllerStatus("manual")]; const button = startButton(); button.focus(); fireEvent.click(button); await flush();
    expect(posts("/faults/activate")).toHaveLength(1);
    expect(posts("/faults/activate")[0]?.body).toEqual({ scenario: "payments_errors", duration_seconds: 120, parameters: { failure_rate: 0.9, status_code: 503 } });
    expect(screen.getByText("Controller confirmed the fault is active.")).toBeTruthy();
    const heading = within(banner()).getByRole("heading", { name: "Payment Errors" });
    expect(heading).toBeTruthy(); expect(within(banner()).getByRole("button", { name: "Stop fault" })).toBeTruthy();
    expect(document.activeElement).toBe(heading);
  });

  it("B posts the selected scenario settings, resets scenario defaults, and preserves duration", async () => {
    await open();
    fireEvent.click(within(manual()).getByRole("radio", { name: "Bad Deployment" }));
    edit("delay_seconds", "2"); edit("duration", "45"); fireEvent.click(startButton()); await flush();
    expect(posts("/faults/activate")[0]?.body).toEqual({ scenario: "bad_deployment", duration_seconds: 45, parameters: { delay_seconds: 2 } });
    // A fresh page lets us inspect the next scenario's independent defaults.
    cleanup(); calls.length = 0; resetFaultActionCooldown(); statuses = [controllerStatus("idle")]; stub(); await open();
    edit("duration", "45"); fireEvent.click(within(manual()).getByRole("radio", { name: "Bad Deployment" }));
    fireEvent.click(within(manual()).getByRole("radio", { name: "Database Exhaustion" }));
    expect(manual().querySelector<HTMLInputElement>('input[name="duration"]')?.value).toBe("45");
    expect(manual().querySelector<HTMLInputElement>('input[name="hold_seconds"]')?.value).toBe("8");
    edit("hold_seconds", "3.5"); fireEvent.click(startButton()); await flush();
    expect(posts("/faults/activate")[0]?.body).toEqual({ scenario: "db_connection_exhaustion", duration_seconds: 45, parameters: { hold_seconds: 3.5 } });
  });

  it("C ignores synchronous and in-flight duplicate starts and blocks Stop while starting", async () => {
    activate = ["hang"];
    await open();
    act(() => { fireEvent.click(startButton()); fireEvent.click(startButton()); });
    await flush(); fireEvent.click(within(banner()).getByRole("button", { name: "Stop" }));
    expect(posts("/faults/activate")).toHaveLength(1); expect(posts("/faults/deactivate")).toHaveLength(0);
    expect(startButton().getAttribute("aria-busy")).toBe("true");
    await act(async () => { held?.(); await Promise.resolve(); }); await flush();
    expect(startButton().getAttribute("aria-busy")).not.toBe("true");
  });

  it("D validates duration and failure rate and focuses invalid input", async () => {
    await open(); const duration = manual().querySelector<HTMLInputElement>('input[name="duration"]')!;
    edit("duration", "0"); fireEvent.click(startButton());
    expect(posts("/faults/activate")).toHaveLength(0); expect(within(manual()).getByText("Enter 0.1–1800 s.")).toBeTruthy();
    expect(duration.getAttribute("aria-invalid")).toBe("true"); expect(document.activeElement).toBe(duration);
    edit("duration", "120"); expect(duration.hasAttribute("aria-invalid")).toBe(false);
    edit("failure_rate", "101"); fireEvent.click(startButton()); expect(within(manual()).getByText("Enter 0–100 %.")).toBeTruthy();
  });

  it.each([
    ["503 target unavailable", { status: 503, body: { error_code: "target_unavailable" } }, "The affected service didn’t acknowledge the start. The fault may be running: check status before trying again."],
    ["network error", "network-error", "The controller didn’t answer. The fault may or may not have started: check status before trying again."],
    ["unconfirming 200", { status: 200, body: controllerStatus("idle") }, "The controller’s answer didn’t confirm the fault. It may be running: check status before trying again."],
  ] as const)("E never reports success or retries after %s", async (_label, outcome, message) => {
    activate = [outcome as Reply]; await open(); fireEvent.click(startButton()); await flush();
    expect(screen.getByText(message)).toBeTruthy(); expect(screen.queryByText("Controller confirmed the fault is active.")).toBeNull();
    expect(within(banner()).queryByRole("button", { name: "Stop fault" })).toBeNull();
    const count = posts("/faults/activate").length; await flush(60_000); expect(posts("/faults/activate")).toHaveLength(count);
    const actionIndex = calls.findIndex((c) => c.method === "POST" && c.path === "/faults/activate");
    expect(calls.slice(actionIndex + 1).some((c) => c.method === "GET" && c.path === "/faults/status")).toBe(true);
  });

  it.each([
    [409, { error_code: "auto_running" }, "An automatic cycle is running. Stop it before starting a manual fault."],
    [422, { error_code: "invalid_settings" }, "The controller rejected these settings. Check the values and try again."],
    [403, { error_code: "forbidden" }, "The controller blocked the request. Open this page from the controller’s own address."],
  ] as const)("F shows refusal %i and retains 422 across polls", async (status, body, message) => {
    activate = [{ status, body }]; statuses = [controllerStatus("idle")]; await open(); fireEvent.click(startButton()); await flush();
    expect(screen.getByText(message)).toBeTruthy(); expect(posts("/faults/activate")).toHaveLength(1);
    if (status === 422) { await flush(10_000); expect(screen.getByText(message)).toBeTruthy(); edit("duration", "121"); expect(screen.queryByText(message)).toBeNull(); }
  });

  it.each([
    ["manual", "One manual fault at a time. Stop the active fault to change parameters."],
    ["automatic", "Manual injection is paused while the automatic cycle runs."],
    ["recovering", "Unavailable while services recover from the last fault."],
    ["unknown", "Unavailable until the fault status is confirmed."],
  ] as const)("G locks controls for %s", async (state, notice) => {
    statuses = [controllerStatus(state)]; await open();
    expect(within(manual()).getByText(notice)).toBeTruthy();
    const controls = [...manual().querySelectorAll<HTMLInputElement>("input")];
    for (const control of controls) { expect(control.getAttribute("aria-disabled")).toBe("true"); expect(control.disabled).toBe(false); }
    for (const input of manual().querySelectorAll<HTMLInputElement>('input[type="text"]')) expect(input.readOnly).toBe(true);
    const button = startButton(); expect(button.getAttribute("aria-disabled")).toBe("true"); button.focus(); fireEvent.click(button);
    const radio = within(manual()).getByRole("radio", { name: "Bad Deployment" }); fireEvent.click(radio);
    expect(posts("/faults/activate")).toHaveLength(0); expect(document.activeElement).toBe(button);
    if (state === "manual") {
      expect(within(manual()).getByText("Active")).toBeTruthy();
      const value = (name: string) => manual().querySelector<HTMLInputElement>(`input[name="${name}"]`)?.value;
      expect([value("failure_rate"), value("duration")]).toEqual(["90", "300"]);
      expect(manual().querySelector<HTMLInputElement>('input[name="status_code"]:checked')?.value).toBe("503");
    }
  });

  it("H confirms Stop and moves focus to recovering heading", async () => {
    statuses = [controllerStatus("manual")]; await open(); statuses = [controllerStatus("recovering")]; const stop = within(banner()).getByRole("button", { name: "Stop fault" }); stop.focus(); fireEvent.click(stop); await flush();
    expect(posts("/faults/deactivate")).toHaveLength(1); expect(posts("/faults/deactivate")[0]?.body).toEqual({ scenario: "payments_errors" });
    expect(screen.getByText("Controller confirmed the fault stopped.")).toBeTruthy();
    const heading = within(banner()).getByRole("heading", { name: "Waiting for services" }); expect(document.activeElement).toBe(heading);
  });

  it("I retains an unconfirmed stop notice and allows a deliberate retry", async () => {
    statuses = [controllerStatus("manual"), controllerStatus("unknown")]; deactivate = [{ status: 503, body: { error_code: "target_unavailable" } }, { status: 503, body: { error_code: "target_unavailable" } }];
    await open(); fireEvent.click(within(banner()).getByRole("button", { name: "Stop fault" })); await flush();
    const message = "The affected service didn’t acknowledge the stop. The fault may still be running: check status, then stop again.";
    expect(screen.getByText(message)).toBeTruthy(); expect(within(banner()).getByRole("heading", { name: "Unknown" })).toBeTruthy();
    const retry = within(banner()).getByRole("button", { name: "Stop again" }); await flush(1000); fireEvent.click(retry); await flush(); expect(posts("/faults/deactivate")).toHaveLength(2);
  });

  it("N sliders move in sensible steps: 1 % for the failure rate, 0.1 s for hold and delay", async () => {
    await open();
    const step = () => manual().querySelector<HTMLInputElement>('input[type="range"]')?.step;
    expect(step()).toBe("1");
    fireEvent.click(within(manual()).getByRole("radio", { name: "Database Exhaustion" })); expect(step()).toBe("0.1");
    fireEvent.click(within(manual()).getByRole("radio", { name: "Bad Deployment" })); expect(step()).toBe("0.1");
  });

  it("P a second click of the same gesture, after the first answer is already in, sends nothing; a deliberate press later does", async () => {
    statuses = [controllerStatus("manual")]; deactivate = [{ status: 503, body: { error_code: "target_unavailable" } }, { status: 200, body: controllerStatus("recovering") }];
    await open(); statuses = [controllerStatus("unknown")];
    fireEvent.click(within(banner()).getByRole("button", { name: "Stop fault" })); await flush(100);
    fireEvent.click(within(banner()).getByRole("button", { name: "Stop again" })); await flush(100);
    expect(posts("/faults/deactivate")).toHaveLength(1);
    await flush(1000); fireEvent.click(within(banner()).getByRole("button", { name: "Stop again" })); await flush(100);
    expect(posts("/faults/deactivate")).toHaveLength(2);
  });

  it("Q a confirmation that the next answer from the controller contradicts is not left on screen", async () => {
    await open(); fireEvent.click(startButton()); await flush(); // the mock controller answers idle again: the fault is already gone
    expect(within(banner()).getByRole("heading", { name: "Idle" })).toBeTruthy();
    expect(screen.queryByText("Controller confirmed the fault is active.")).toBeNull();
    await flush(5000); expect(screen.queryByText("Controller confirmed the fault is active.")).toBeNull();
  });

  it("R a fault the controller is still starting is shown as starting, never as active", async () => {
    const base = controllerStatus("manual"); statuses = [controllerStatus("manual", { active: { ...base.active!, status: "activating", acknowledged_at: null } })];
    await open();
    expect(within(banner()).getByRole("heading", { name: "Payment Errors" })).toBeTruthy();
    expect(banner().textContent).toContain("Starting: waiting for the affected service to acknowledge the fault.");
    expect(within(manual()).queryByText("Active")).toBeNull(); expect(within(manual()).getByText("Starting…")).toBeTruthy();
  });

  it("S an old status is re-read before anything is sent: no POST on a stale page", async () => {
    statuses = [controllerStatus("idle"), "hang"]; await open();
    await flush(8000); // the polls since then never answered
    const reads = calls.filter((c) => c.method === "GET" && c.path === "/faults/status").length;
    fireEvent.click(startButton()); await flush();
    expect(posts("/faults/activate")).toHaveLength(0);
    expect(screen.getByText("The controller’s state is being re-read. Try again in a moment.")).toBeTruthy();
    expect(calls.filter((c) => c.method === "GET" && c.path === "/faults/status").length).toBeGreaterThan(reads);
  });

  it("T after an uncertain stop whose Stop button then disappears, focus goes to the banner heading, not the page", async () => {
    statuses = [controllerStatus("manual")]; deactivate = ["network-error"]; await open(); statuses = [controllerStatus("recovering")];
    const stop = within(banner()).getByRole("button", { name: "Stop fault" }); stop.focus(); fireEvent.click(stop); await flush();
    expect(screen.getByText("The controller didn’t answer. The fault may still be running: check status, then stop again.")).toBeTruthy();
    expect(within(banner()).queryByRole("button", { name: "Stop fault" })).toBeNull();
    expect(document.activeElement).toBe(within(banner()).getByRole("heading", { name: "Waiting for services" }));
  });

  it("U a Stop is never blocked by the page: it is sent while a start is still in flight", async () => {
    activate = ["hang"]; await open();
    fireEvent.click(startButton()); await flush(100);
    statuses = [(() => { const base = controllerStatus("manual"); return controllerStatus("manual", { active: { ...base.active!, status: "activating", acknowledged_at: null } }); })()];
    await flush(3000); // the poll now shows the fault the controller is starting
    const stop = within(banner()).getByRole("button", { name: "Stop fault" });
    expect(stop.getAttribute("aria-disabled")).not.toBe("true");
    fireEvent.click(stop); await flush(100);
    expect(posts("/faults/activate")).toHaveLength(1); expect(posts("/faults/deactivate")).toHaveLength(1);
  });

  it("V a Stop on an old answer is still sent (stopping is always safe; only starts wait for a fresh answer)", async () => {
    statuses = [controllerStatus("manual")]; await open();
    focusManager.setFocused(false); await flush(8000); // a hidden tab pauses polling: the answer on screen ages
    const stop = within(banner()).getByRole("button", { name: "Stop fault" });
    expect(stop.getAttribute("aria-disabled")).not.toBe("true");
    fireEvent.click(stop); await flush(100);
    expect(posts("/faults/deactivate")).toHaveLength(1);
  });

  it("W a read-back that cannot start (browser reports offline) does not leave the page busy, and still reads the controller", async () => {
    statuses = [controllerStatus("manual")]; await open();
    onlineManager.setOnline(false);
    try {
      statuses = [controllerStatus("recovering")];
      const before = calls.filter((c) => c.method === "GET" && c.path === "/faults/status").length;
      fireEvent.click(within(banner()).getByRole("button", { name: "Stop fault" })); await flush(100);
      await flush(7000);
      expect(calls.filter((c) => c.method === "GET" && c.path === "/faults/status").length).toBeGreaterThan(before);
      expect(within(banner()).getByRole("heading", { name: "Waiting for services" })).toBeTruthy();
    } finally { onlineManager.setOnline(true); }
  });

  it("X a confirmed start whose incident the read-back no longer shows is not left on screen", async () => {
    const other = (() => { const base = controllerStatus("manual"); return controllerStatus("manual", { active: { ...base.active!, incident_id: "inc-00000000-0000-4000-8000-000000000099", status: "activating", acknowledged_at: null } }); })();
    await open(); statuses = [other]; fireEvent.click(startButton()); await flush();
    expect(screen.queryByText("Controller confirmed the fault is active.")).toBeNull();
  });

  it("Y a confirmed stop is not left on screen when the read-back shows a cycle with a fault", async () => {
    const running = controllerStatus("automatic");
    statuses = [controllerStatus("manual")]; await open(); statuses = [running];
    fireEvent.click(within(banner()).getByRole("button", { name: "Stop fault" })); await flush();
    expect(screen.queryByText("Controller confirmed the fault stopped.")).toBeNull();
  });

  it("Z a refresh-only press on an unavailable button leaves other notices alone", async () => {
    activate = [{ status: 422, body: { error_code: "validation_error" } }]; statuses = [controllerStatus("idle")]; await open();
    fireEvent.click(startButton()); await flush(100);
    const message = "The controller rejected these settings. Check the values and try again.";
    expect(screen.getByText(message)).toBeTruthy();
    focusManager.setFocused(false); await flush(8000); fireEvent.click(within(banner()).getByRole("button", { name: "Stop" })); await flush(100);
    expect(screen.getByText(message)).toBeTruthy();
  });

  it("AA the double-click guard survives a remount, and a held Enter key does not repeat an action", async () => {
    resetFaultActionCooldown();
    statuses = [controllerStatus("manual")]; deactivate = [{ status: 503, body: { error_code: "target_unavailable" } }, { status: 200, body: controllerStatus("recovering") }];
    await open(); statuses = [controllerStatus("unknown")];
    fireEvent.click(within(banner()).getByRole("button", { name: "Stop fault" })); await flush(100);
    cleanup(); await open();
    fireEvent.click(within(banner()).getByRole("button", { name: "Stop again" })); await flush(100);
    expect(posts("/faults/deactivate")).toHaveLength(1);
    const stopAgain = within(banner()).getByRole("button", { name: "Stop again" });
    expect(fireEvent.keyDown(stopAgain, { key: "Enter", repeat: true })).toBe(false);
    expect(fireEvent.keyDown(stopAgain, { key: "Enter" })).toBe(true);
  });

  it("AB focus the operator moved elsewhere is not taken back when the pressed button disappears", async () => {
    statuses = [controllerStatus("manual")]; deactivate = ["network-error"]; await open(); statuses = [controllerStatus("recovering")];
    const link = screen.getByRole("link", { name: /Storefront/ });
    const stop = within(banner()).getByRole("button", { name: "Stop fault" }); stop.focus(); fireEvent.click(stop); link.focus(); await flush();
    expect(within(banner()).queryByRole("button", { name: "Stop fault" })).toBeNull();
    expect(document.activeElement).toBe(link);
  });

  it("J idle Stop is unavailable and keeps focus", async () => {
    await open(); const stop = within(banner()).getByRole("button", { name: "Stop" }); stop.focus(); fireEvent.click(stop);
    expect(posts("/faults/deactivate")).toHaveLength(0); expect(document.activeElement).toBe(stop);
  });

  it("K clears confirmed notice when state moves to recovering but retains it while manual", async () => {
    await open(); statuses = [controllerStatus("manual")]; fireEvent.click(startButton()); await flush();
    expect(screen.getByText("Controller confirmed the fault is active.")).toBeTruthy();
    await flush(5000); expect(screen.getByText("Controller confirmed the fault is active.")).toBeTruthy();
    statuses = [controllerStatus("recovering")]; await flush(5000);
    expect(screen.queryByText("Controller confirmed the fault is active.")).toBeNull();
  });

  it("L limits POSTs to the two action routes and sends JSON without credentials", async () => {
    statuses = [controllerStatus("manual")]; await open(); fireEvent.click(within(banner()).getByRole("button", { name: "Stop fault" })); await flush();
    const actionCalls = calls.filter((call) => call.method === "POST");
    expect(actionCalls.map((call) => call.path)).toEqual(["/faults/deactivate"]);
    expect(actionCalls.every((call) => call.headers.get("content-type")?.includes("application/json"))).toBe(true);
    expect(actionCalls.every((call) => call.headers.get("cookie") === null)).toBe(true);
  });

  it("M times out a held action after 15 seconds without retrying", async () => {
    let fetches = 0;
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      fetches++;
      // openapi-fetch hands over a Request: its signal is the one to watch.
      const signal = input instanceof Request ? input.signal : init?.signal;
      return new Promise<Response>((_resolve, reject) => signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
    }));
    const result = postStart({ scenario: "payments_errors", duration_seconds: 120, parameters: { failure_rate: 0.9, status_code: 503 } });
    await flush(15_000);
    await expect(result).resolves.toMatchObject({ kind: "unconfirmed", message: expect.stringContaining("The controller didn’t answer in time.") });
    expect(fetches).toBe(1);
  });
});
