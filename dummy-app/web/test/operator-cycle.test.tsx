// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8090/"}
import { focusManager } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { controllerStatus, type ControllerStatus } from "../e2e/fixtures/controller.ts";
import { OPERATOR_UI_CONFIG } from "../e2e/fixtures/gateway.ts";
import { operatorRoutes } from "../src/operator/routes.tsx";
import { resetFaultActionCooldown } from "../src/operator/manual/use-fault-actions.ts";
import { AppProviders } from "../src/shared/AppProviders.tsx";

type Answer<T> = T | "network-error" | "hang" | Response;
type Reply = { status: number; body: unknown; after?: Promise<void> } | "network-error" | "hang";
type Call = { method: string; path: string; body?: unknown; headers: Headers };
let statuses: Answer<ControllerStatus>[] = [];
let activate: Reply[] = [];
let deactivate: Reply[] = [];
let autoStart: Reply[] = [];
let autoStop: Reply[] = [];
const calls: Call[] = [];
let held: (() => void) | null = null;
function next<T>(answers: Answer<T>[]): Answer<T> { return answers.length > 1 ? answers.shift() as Answer<T> : answers[0] as Answer<T>; }
function reply<T>(answer: Answer<T>, request: Request): Promise<Response> | Response {
  if (answer === "network-error") return Promise.reject(new TypeError("Failed to fetch"));
  if (answer === "hang") return new Promise<Response>((resolve, reject) => { request.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))); held = () => resolve(Response.json(controllerStatus("idle"))); });
  if (answer instanceof Response) return answer;
  return Response.json(answer);
}
function actionReply(answer: Reply, request: Request): Promise<Response> | Response {
  if (answer === "network-error") return Promise.reject(new TypeError("Failed to fetch"));
  if (answer === "hang") return new Promise<Response>((resolve, reject) => { request.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))); held = () => resolve(Response.json(controllerStatus("idle"))); });
  if (answer.after) return answer.after.then(() => Response.json(answer.body, { status: answer.status }));
  return Response.json(answer.body, { status: answer.status });
}
function take(q: Reply[]) { return q.length > 1 ? q.shift()! : q[0]!; }
function stub() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.href), init);
    const url = new URL(request.url); let body: unknown;
    if (request.method === "POST") { const raw = await request.clone().text(); body = raw ? JSON.parse(raw) : undefined; }
    calls.push({ method: request.method, path: url.pathname, body, headers: request.headers });
    if (request.method === "POST") {
      if (url.pathname === "/faults/activate") return actionReply(take(activate), request);
      if (url.pathname === "/faults/deactivate") return actionReply(take(deactivate), request);
      if (url.pathname === "/faults/auto/start") return actionReply(take(autoStart), request);
      if (url.pathname === "/faults/auto/stop") return actionReply(take(autoStop), request);
      throw new Error(`Unexpected POST ${url.pathname}`);
    }
    if (url.pathname === "/ui/config") return Response.json(OPERATOR_UI_CONFIG);
    if (url.pathname === "/faults/status") return reply(next(statuses), request);
    if (url.pathname === "/services/health") return Response.json({ checked_at: new Date().toISOString(), metrics: "available", services: [] });
    if (url.pathname === "/activity" && url.search === "?limit=200") return Response.json({ capacity: 200, entries: [] });
    throw new Error(`Unexpected GET ${url.pathname}${url.search}`);
  }));
}
const flush = async (ms = 60) => act(async () => { await vi.advanceTimersByTimeAsync(ms); for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(1); });
async function open() { render(<AppProviders><RouterProvider router={createMemoryRouter(operatorRoutes, { initialEntries: ["/"] })} /></AppProviders>); await flush(); await flush(); }
const banner = () => screen.getByRole("region", { name: "Experiment" });
const cycle = () => screen.getByRole("region", { name: "Automatic cycle" });
const manual = () => screen.getByRole("region", { name: "Manual fault injection" });
const start = () => within(cycle()).getByRole("button", { name: "Start cycle" });
const posts = (path?: string) => calls.filter((c) => c.method === "POST" && (!path || c.path === path));
const field = (name: string) => cycle().querySelector<HTMLInputElement>(`input[name="${name}"]`)!;
const edit = (name: string, value: string) => fireEvent.change(field(name), { target: { value } });
const consistent = (body: any) => { const base = controllerStatus("automatic"); return controllerStatus("automatic", { auto: { ...base.auto!, seed: body.seed, shuffle: body.shuffle, repeats: body.repeat, repeat: 1, config: body as never, phase: "warmup", step: 0, steps: body.repeat * body.scenarios.length, scenario: body.scenarios[0] } }); };
const defaultRequest = { seed: 42, scenarios: ["db_connection_exhaustion", "payments_errors", "bad_deployment"], shuffle: false, warmup_seconds: 120, duration_seconds: 120, recovery_seconds: 90, repeat: 1 };
beforeEach(() => { vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"] }); statuses = [controllerStatus("idle")]; activate = [{ status: 200, body: controllerStatus("manual") }]; deactivate = [{ status: 200, body: controllerStatus("recovering") }]; autoStart = [{ status: 409, body: { error_code: "controller_busy" } }]; autoStop = [{ status: 200, body: controllerStatus("recovering") }]; calls.length = 0; held = null; resetFaultActionCooldown(); stub(); focusManager.setFocused(true); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); focusManager.setFocused(undefined); });

describe("automatic cycle panel", () => {
  it("A shows the planned defaults", async () => { await open(); expect(field("warmup").value).toBe("120"); expect(field("duration").value).toBe("120"); expect(field("recovery").value).toBe("90"); expect(field("repeat").value).toBe("1"); expect(cycle().querySelectorAll('input[name="scenarios"]:checked')).toHaveLength(3); expect(within(cycle()).getByRole("switch").getAttribute("aria-checked")).toBe("false"); expect(field("seed").value).toBe("42"); expect(within(cycle()).getByText("Total 12 min 30 s")).toBeTruthy(); expect(within(cycle()).getByRole("img", { name: /^Planned schedule: 12 min 30 s in total, 3 faults/ })).toBeTruthy(); expect(screen.queryByText(/sample data/i)).toBeNull(); });
  it("B sends the selected settings and reflects the confirmed running cycle", async () => { await open(); edit("warmup", "60"); edit("duration", "30"); edit("recovery", "0"); edit("repeat", "3"); fireEvent.click(within(cycle()).getByRole("checkbox", { name: "Bad Deployment" })); fireEvent.click(within(cycle()).getByRole("switch")); edit("seed", "7341"); const body = { seed: 7341, scenarios: ["db_connection_exhaustion", "payments_errors"], shuffle: true, warmup_seconds: 60, duration_seconds: 30, recovery_seconds: 0, repeat: 3 }; autoStart = [{ status: 200, body: consistent(body) }]; statuses = [consistent(body)]; start().focus(); fireEvent.click(start()); await flush(); expect(posts("/faults/auto/start")).toHaveLength(1); expect(posts("/faults/auto/start")[0]?.body).toEqual(body); expect(posts("/faults/auto/start")[0]?.headers.get("content-type")).toMatch(/application\/json/); expect(screen.getByText("Controller confirmed the cycle is running.")).toBeTruthy(); expect(within(banner()).getByRole("button", { name: "Stop cycle" })).toBeTruthy(); expect(within(cycle()).getByText("Cycle running. Settings are locked until it stops.")).toBeTruthy(); expect(document.activeElement).toBe(within(banner()).getByRole("heading", { name: "Warmup" })); });
  it("C steppers enforce limits and recover from invalid text", async () => { await open(); fireEvent.click(within(cycle()).getByRole("button", { name: "Increase warmup" })); expect(field("warmup").value).toBe("130"); fireEvent.click(within(cycle()).getByRole("button", { name: "Decrease warmup" })); expect(field("warmup").value).toBe("120"); const down = within(cycle()).getByRole("button", { name: "Decrease repeats" }); expect(down.getAttribute("aria-disabled")).toBe("true"); down.focus(); fireEvent.click(down); expect(document.activeElement).toBe(down); edit("repeat", "1000"); expect(within(cycle()).getByRole("button", { name: "Increase repeats" }).getAttribute("aria-disabled")).toBe("true"); edit("warmup", "bad"); fireEvent.click(within(cycle()).getByRole("button", { name: "Increase warmup" })); expect(field("warmup").value).toBe("130"); });
  it("D validates fields in order and clears only an edited error", async () => { await open(); for (const box of within(cycle()).getAllByRole("checkbox")) fireEvent.click(box); fireEvent.click(start()); const set = cycle().querySelector("fieldset")!; expect(within(cycle()).getByText("Choose at least one scenario.")).toBeTruthy(); expect(set.getAttribute("aria-describedby")).toBeTruthy(); expect(posts()).toHaveLength(0); edit("warmup", "4000"); fireEvent.click(start()); expect(within(cycle()).getByText("Enter 0–3600 s.")).toBeTruthy(); expect(field("warmup").getAttribute("aria-invalid")).toBe("true"); expect(document.activeElement).toBe(field("warmup")); edit("duration", "bad"); fireEvent.click(start()); edit("warmup", "120"); expect(within(cycle()).queryByText("Enter 0–3600 s.")).toBeNull(); expect(within(cycle()).getByText(/Enter .* s\./)).toBeTruthy(); });
  it("E ignores synchronous and in-flight duplicate presses, applies cooldown, then allows a new press", async () => { autoStart = ["hang", { status: 409, body: { error_code: "controller_busy" } }]; await open(); act(() => { fireEvent.click(start()); fireEvent.click(start()); }); await flush(); expect(posts("/faults/auto/start")).toHaveLength(1); expect(start().getAttribute("aria-busy")).toBe("true"); await act(async () => { held?.(); await Promise.resolve(); }); await flush(); expect(start().getAttribute("aria-busy")).not.toBe("true"); fireEvent.click(start()); await flush(); expect(posts("/faults/auto/start")).toHaveLength(1); await flush(1000); fireEvent.click(start()); await flush(); expect(posts("/faults/auto/start")).toHaveLength(2); });
  it.each([[{ status: 503, body: { error_code: "target_unavailable" } }, "The controller failed while handling the start. The cycle may be running: check status before trying again."], ["network-error", "The controller didn’t answer. The cycle may or may not have started: check status before trying again."], [{ status: 200, body: controllerStatus("idle") }, "The controller’s answer didn’t confirm the cycle. It may be running: check status before trying again."], [{ status: 200, body: (() => { const b = consistent(defaultRequest); return controllerStatus("automatic", { auto: { ...b.auto!, config: { ...defaultRequest, seed: 43 } as never } }); })() }, "The controller’s answer didn’t confirm the cycle. It may be running: check status before trying again."] ] as const)("F treats uncertain reply as uncertain", async (outcome, message) => { autoStart = [outcome as Reply]; await open(); fireEvent.click(start()); await flush(); expect(screen.getByText(message)).toBeTruthy(); expect(screen.queryByText(/Controller confirmed/)).toBeNull(); expect(within(banner()).queryByRole("button", { name: "Stop cycle" })).toBeNull(); const before = posts("/faults/auto/start").length; await flush(60_000); expect(posts("/faults/auto/start")).toHaveLength(before); const i = calls.findIndex((c) => c.method === "POST" && c.path === "/faults/auto/start"); expect(calls.slice(i + 1).some((c) => c.method === "GET" && c.path === "/faults/status")).toBe(true); });
  it.each([[409, { error_code: "controller_busy" }, "A cycle or a fault is already running. Stop it before starting a cycle."], [422, { error_code: "invalid_settings" }, "The controller rejected these settings. Check the values and try again."], [403, { error_code: "forbidden" }, "The controller blocked the request. Open this page from the controller’s own address."]] as const)("G renders refusal %i", async (status, body, message) => { autoStart = [{ status, body }]; await open(); fireEvent.click(start()); await flush(); expect(screen.getByText(message)).toBeTruthy(); expect(posts("/faults/auto/start")).toHaveLength(1); if (status === 422) { await flush(5000); expect(screen.getByText(message)).toBeTruthy(); edit("duration", "121"); expect(screen.queryByText(message)).toBeNull(); } });
  it.each([["manual", "Unavailable while a manual fault is active."], ["recovering", "Unavailable while services recover from the last fault."], ["unknown", "Unavailable until the fault status is confirmed."]] as const)("H locks settings in %s state", async (state, notice) => { statuses = [controllerStatus(state)]; await open(); expect(within(cycle()).getByText(notice)).toBeTruthy(); const controls = [...cycle().querySelectorAll<HTMLInputElement | HTMLButtonElement | HTMLButtonElement>('input[name="warmup"], input[name="duration"], input[name="recovery"], input[name="repeat"], input[name="seed"], button[role="switch"], button[aria-label^="Increase"], button[aria-label^="Decrease"], button')]; const button = start(); expect(button.getAttribute("aria-disabled")).toBe("true"); for (const c of controls) if (c !== button) expect(c.getAttribute("aria-disabled")).toBe("true"); for (const input of cycle().querySelectorAll<HTMLInputElement>('input[type="text"]')) expect(input.readOnly).toBe(true); button.focus(); fireEvent.click(button); expect(posts()).toHaveLength(0); expect(document.activeElement).toBe(button); });
  it("H locks controls while disconnected", async () => { statuses = ["network-error", "network-error"]; await open(); expect(within(cycle()).getByText("Unavailable while the controller is unreachable.")).toBeTruthy(); const button = start(); expect(button.getAttribute("aria-disabled")).toBe("true"); expect(posts()).toHaveLength(0); });
  it("I reflects the controller’s automatic schedule and progress", async () => { statuses = [controllerStatus("automatic")]; await open(); expect(field("warmup").value).toBe("60"); expect(field("duration").value).toBe("120"); expect(field("recovery").value).toBe("180"); expect(field("repeat").value).toBe("3"); expect(field("seed").value).toBe("7341"); expect(within(cycle()).getByRole("switch").getAttribute("aria-checked")).toBe("true"); expect(within(cycle()).getByRole<HTMLInputElement>("checkbox", { name: "Database Exhaustion" }).checked).toBe(true); expect(within(cycle()).getByRole<HTMLInputElement>("checkbox", { name: "Payment Errors" }).checked).toBe(true); expect(within(cycle()).getByRole<HTMLInputElement>("checkbox", { name: "Bad Deployment" }).checked).toBe(false); expect(within(cycle()).getByText("Repeat 2 of 3 · fault phase")).toBeTruthy(); const segments = [...cycle().querySelectorAll("[data-state]")]; expect(segments.filter((s) => s.getAttribute("data-state") === "now")).toHaveLength(1); expect(segments.some((s) => s.getAttribute("data-kind") === "fault" && s.getAttribute("data-state") === "now")).toBe(true); expect(segments.some((s) => s.getAttribute("data-state") === "done")).toBe(true); expect(start().getAttribute("aria-disabled")).toBe("true"); expect(within(banner()).getByRole("heading", { name: "Payment Errors" })).toBeTruthy(); expect(within(banner()).getByRole("button", { name: "Stop cycle" })).toBeTruthy(); });
  it("J confirms stop and retains target refusal through status readback, with Stop again", async () => { statuses = [controllerStatus("automatic"), controllerStatus("recovering")]; await open(); const stop = within(banner()).getByRole("button", { name: "Stop cycle" }); stop.focus(); fireEvent.click(stop); await flush(); expect(posts("/faults/auto/stop")).toHaveLength(1); expect(posts("/faults/auto/stop")[0]?.body).toBeUndefined(); expect(screen.getByText("Controller confirmed the cycle stopped.")).toBeTruthy(); expect(within(banner()).getByRole("heading", { name: "Waiting for services" })).toBeTruthy(); expect(document.activeElement).toBe(within(banner()).getByRole("heading", { name: "Waiting for services" })); });
  it("J retains target refusal and Stop again uses the manual deactivate route", async () => { statuses = [controllerStatus("automatic"), controllerStatus("unknown")]; autoStop = [{ status: 503, body: { error_code: "target_unavailable" } }]; await open(); fireEvent.click(within(banner()).getByRole("button", { name: "Stop cycle" })); await flush(); expect(screen.getByText("The cycle was told to stop, but the affected service didn’t acknowledge stopping its fault. The fault may still be running: check status, then use Stop again.")).toBeTruthy(); expect(within(banner()).getByRole("heading", { name: "Unknown" })).toBeTruthy(); await flush(1000); fireEvent.click(within(banner()).getByRole("button", { name: "Stop again" })); await flush(); expect(posts("/faults/deactivate")).toHaveLength(1); expect(posts("/faults/deactivate")[0]?.body).toEqual({ scenario: "payments_errors" }); expect(posts("/faults/auto/stop")).toHaveLength(1); });
  it("K Stop cycle bypasses a held cycle start and an aged answer", async () => { autoStart = ["hang"]; await open(); fireEvent.click(start()); await flush(100); statuses = [controllerStatus("automatic")]; await flush(3000); fireEvent.click(within(banner()).getByRole("button", { name: "Stop cycle" })); await flush(100); expect(posts("/faults/auto/start")).toHaveLength(1); expect(posts("/faults/auto/stop")).toHaveLength(1); cleanup(); resetFaultActionCooldown(); calls.length = 0; statuses = [controllerStatus("automatic")]; autoStop = [{ status: 200, body: controllerStatus("recovering") }]; stub(); await open(); focusManager.setFocused(false); await flush(8000); fireEvent.click(within(banner()).getByRole("button", { name: "Stop cycle" })); await flush(100); expect(posts("/faults/auto/stop")).toHaveLength(1); });
  it("K a Stop cycle that the controller allows is sent even while it reports the cycle as stopping", async () => {
    autoStop = [{ status: 200, body: controllerStatus("recovering") }]; statuses = [controllerStatus("automatic", { auto: null })]; await open();
    const stop = within(banner()).getByRole("button", { name: "Stop cycle" }); expect(stop.getAttribute("aria-disabled")).not.toBe("true");
    fireEvent.click(stop); await flush(100); expect(posts("/faults/auto/stop")).toHaveLength(1);
  });

  it("K2 an unconfirmed fault while the cycle still runs keeps a usable Stop cycle, and Stop again stays unavailable", async () => {
    const base = controllerStatus("unknown");
    statuses = [controllerStatus("unknown", { auto_running: true, controls: { ...base.controls, auto_stop: { enabled: true, reason: null }, deactivate: { enabled: false, reason: "auto_running" } } })];
    autoStop = [{ status: 200, body: controllerStatus("recovering") }]; await open();
    expect(within(banner()).getByRole("button", { name: "Check status" })).toBeTruthy();
    fireEvent.click(within(banner()).getByRole("button", { name: "Stop cycle" })); await flush(100);
    expect(posts("/faults/auto/stop")).toHaveLength(1); expect(posts("/faults/deactivate")).toHaveLength(0);
  });

  it("R1 a Start answered after a Stop was pressed never undoes the Stop or claims the cycle is running", async () => {
    const cfg = { seed: 42, scenarios: ["db_connection_exhaustion", "payments_errors", "bad_deployment"], shuffle: false, warmup_seconds: 120, duration_seconds: 120, recovery_seconds: 90, repeat: 1 };
    const running = (config: object) => controllerStatus("automatic", { auto: { ...controllerStatus("automatic").auto!, config: config as never, repeats: 1, seed: 42, shuffle: false } });
    let release: () => void = () => undefined; const gate = new Promise<void>((resolve) => { release = resolve; });
    statuses = [controllerStatus("idle")]; autoStart = [{ status: 200, body: running(cfg), after: gate }]; autoStop = [{ status: 200, body: controllerStatus("recovering") }];
    await open(); fireEvent.click(start()); await flush(100);
    statuses = [running(cfg)]; await flush(3000); // another tab started it: the controller now reports the cycle
    statuses = [controllerStatus("recovering")];
    fireEvent.click(within(banner()).getByRole("button", { name: "Stop cycle" })); await flush(100);
    expect(posts("/faults/auto/stop")).toHaveLength(1);
    release(); await flush(200);
    expect(screen.queryByText("Controller confirmed the cycle is running.")).toBeNull();
    expect(within(banner()).getByRole("heading", { name: "Waiting for services" })).toBeTruthy();
    expect(screen.getByText("This start was answered after a stop was pressed, so it isn’t shown as confirmed. The status above is the controller’s current state.")).toBeTruthy();
  });

  it("R2 a confirmed cycle that the read-back shows replaced by a different one is not left on screen", async () => {
    const cfg = { seed: 42, scenarios: ["db_connection_exhaustion", "payments_errors", "bad_deployment"], shuffle: false, warmup_seconds: 120, duration_seconds: 120, recovery_seconds: 90, repeat: 1 };
    const running = (config: object) => controllerStatus("automatic", { auto: { ...controllerStatus("automatic").auto!, config: config as never, repeats: 1, seed: (config as { seed: number }).seed, shuffle: false } });
    statuses = [controllerStatus("idle")]; autoStart = [{ status: 200, body: running(cfg) }]; await open();
    statuses = [running({ ...cfg, seed: 43 })]; fireEvent.click(start()); await flush(100);
    expect(screen.queryByText("Controller confirmed the cycle is running.")).toBeNull();
    await flush(5000); expect(screen.queryByText("Controller confirmed the cycle is running.")).toBeNull();
  });

  it("S1 a running cycle's schedule and total use the controller's exact numbers, whatever the seed", async () => {
    const base = controllerStatus("automatic");
    statuses = [controllerStatus("automatic", { auto: { ...base.auto!, seed: 2147483648, config: { ...base.auto!.config, seed: 2147483648, warmup_seconds: 0.004, duration_seconds: 0.104, recovery_seconds: 0.004, repeat: 1000, scenarios: ["payments_errors"] } } })];
    await open();
    expect(within(cycle()).getByText("Total 1 min 48 s")).toBeTruthy();
    expect(within(cycle()).getByText("Too many steps to draw.")).toBeTruthy();
  });

  it("S2 the schedule image says where the cycle is, not only how long it is", async () => {
    statuses = [controllerStatus("automatic")]; await open();
    expect(within(cycle()).getByRole("img", { name: "Planned schedule: 31 min in total, 6 faults. Now: step 4 of 6, fault phase." })).toBeTruthy();
  });

  it("L rereads stale status without posting a start", async () => { statuses = [controllerStatus("idle"), "hang"]; await open(); focusManager.setFocused(false); await flush(8000); fireEvent.click(start()); await flush(); expect(posts("/faults/auto/start")).toHaveLength(0); expect(screen.getByText("The controller’s state is being re-read. Try again in a moment.")).toBeTruthy(); expect(calls.some((c) => c.method === "GET" && c.path === "/faults/status")).toBe(true); });
  it("M does not draw more than 60 steps and hides the schedule for invalid values", async () => { await open(); edit("repeat", "21"); expect(within(cycle()).getByText("Too many steps to draw.")).toBeTruthy(); expect(within(cycle()).getByText("Total 3 h 42 min 30 s")).toBeTruthy(); expect(cycle().querySelector('[role="img"]')).toBeNull(); edit("warmup", "4000"); expect(within(cycle()).getByText("Total —")).toBeTruthy(); expect(cycle().querySelector('[role="img"]')).toBeNull(); });
  it("N blocks Enter key repeat on Start cycle but allows a fresh Enter", async () => { await open(); const button = start(); expect(fireEvent.keyDown(button, { key: "Enter", repeat: true })).toBe(false); expect(fireEvent.keyDown(button, { key: "Enter", repeat: false })).toBe(true); });
  it("N blocks Enter key repeat on Stop cycle but allows a fresh Enter", async () => { statuses = [controllerStatus("automatic")]; await open(); const button = within(banner()).getByRole("button", { name: "Stop cycle" }); expect(fireEvent.keyDown(button, { key: "Enter", repeat: true })).toBe(false); expect(fireEvent.keyDown(button, { key: "Enter", repeat: false })).toBe(true); });
  it("O limits POSTs to action routes and sets JSON content type", async () => { await open(); edit("duration", "30"); fireEvent.click(start()); await flush(); expect(posts().map((c) => c.path)).toEqual(["/faults/auto/start"]); for (const p of posts()) if (p.body !== undefined) expect(p.headers.get("content-type")).toMatch(/application\/json/); expect(posts("/faults/activate")).toHaveLength(0); });
  it("P pauses manual injection while a cycle runs", async () => { statuses = [controllerStatus("automatic")]; await open(); expect(within(manual()).getByText("Manual injection is paused while the automatic cycle runs.")).toBeTruthy(); expect(within(manual()).getByRole("button", { name: "Inject fault" }).getAttribute("aria-disabled")).toBe("true"); });
});





