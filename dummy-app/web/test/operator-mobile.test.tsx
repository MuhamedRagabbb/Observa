// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8090/ui/next/"}
import { focusManager } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { controllerStatus, type ControllerStatus } from "../e2e/fixtures/controller.ts";
import { OPERATOR_UI_CONFIG } from "../e2e/fixtures/gateway.ts";
import { resetFaultActionCooldown } from "../src/operator/manual/use-fault-actions.ts";
import { operatorRoutes } from "../src/operator/routes.tsx";
import { AppProviders } from "../src/shared/AppProviders.tsx";

type Call = { method: string; path: string; body?: unknown };
let status: ControllerStatus = controllerStatus("idle");
let activateAnswer: Promise<void> | null = null;
const calls: Call[] = [];

let isPhone = false;
const mediaListeners = new Set<() => void>();
function phone(matches: boolean) {
  isPhone = matches;
  vi.stubGlobal("matchMedia", (query: string) => ({
    get matches() { return isPhone && query.includes("max-width"); }, media: query, onchange: null,
    addEventListener(_type: string, listener: () => void) { mediaListeners.add(listener); },
    removeEventListener(_type: string, listener: () => void) { mediaListeners.delete(listener); },
    addListener() {}, removeListener() {}, dispatchEvent: () => false,
  }));
}
/** The window crosses the phone breakpoint while the page is open (resize or zoom). */
function crossBreakpoint(matches: boolean) { isPhone = matches; act(() => { for (const listener of [...mediaListeners]) listener(); }); }
function stub() {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.href), init);
    const url = new URL(request.url);
    let body: unknown;
    if (request.method === "POST") { const raw = await request.clone().text(); body = raw ? JSON.parse(raw) : undefined; }
    calls.push({ method: request.method, path: url.pathname, body });
    if (request.method === "POST") {
      if (url.pathname === "/faults/activate") { await activateAnswer; return Response.json(controllerStatus("manual")); }
      if (url.pathname === "/faults/deactivate" || url.pathname === "/faults/auto/stop") return Response.json(controllerStatus("recovering"));
      throw new Error(`Unexpected POST ${url.pathname}`);
    }
    if (url.pathname === "/ui/config") return Response.json(OPERATOR_UI_CONFIG);
    if (url.pathname === "/faults/status") return Response.json(status);
    if (url.pathname === "/services/health") return Response.json({ checked_at: new Date().toISOString(), metrics: "available", services: [] });
    if (url.pathname === "/activity") return Response.json({ capacity: 200, entries: [] });
    throw new Error(`Unexpected GET ${url.pathname}${url.search}`);
  }));
}
const flush = async (ms = 60) => act(async () => { await vi.advanceTimersByTimeAsync(ms); for (let i = 0; i < 5; i++) await vi.advanceTimersByTimeAsync(1); });
async function open() { render(<AppProviders><RouterProvider router={createMemoryRouter(operatorRoutes, { initialEntries: ["/"] })} /></AppProviders>); await flush(); await flush(); }
const posts = (path: string) => calls.filter((c) => c.method === "POST" && c.path === path);
const toggle = (name: string | RegExp) => screen.getByRole("button", { name });
const bar = () => screen.queryByRole("region", { name: "Run in progress" });

beforeEach(() => { vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"] }); status = controllerStatus("idle"); calls.length = 0; activateAnswer = null; mediaListeners.clear(); isPhone = false; resetFaultActionCooldown(); stub(); focusManager.setFocused(true); });
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); focusManager.setFocused(undefined); });

describe("phone disclosure sections", () => {
  it("idle: manual open with its summary, cycle collapsed with its summary and out of reach", async () => {
    phone(true); await open();
    const manual = toggle(/^Manual fault injection/);
    expect(manual.tagName).toBe("BUTTON");
    expect(manual.getAttribute("aria-expanded")).toBe("true");
    expect(manual.textContent).toContain("F2 · 90% · HTTP 503 · 120 s");
    const cycle = toggle(/^Automatic cycle/);
    expect(cycle.getAttribute("aria-expanded")).toBe("false");
    expect(cycle.textContent).toContain("1 repeat · 12 min 30 s");
    expect(document.getElementById(cycle.getAttribute("aria-controls")!)).not.toBeNull();
    expect(screen.queryByRole("button", { name: "Start cycle" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Bad Deployment" })).toBeNull();
    expect(screen.getByRole("button", { name: "Inject fault" })).toBeTruthy();
  });
  it("a section opens and closes by its button and keeps focus on it", async () => {
    phone(true); await open();
    const cycle = toggle(/^Automatic cycle/);
    cycle.focus(); fireEvent.click(cycle);
    expect(cycle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: "Start cycle" })).toBeTruthy();
    expect(document.activeElement).toBe(cycle);
    fireEvent.click(cycle);
    expect(cycle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: "Start cycle" })).toBeNull();
    expect(document.activeElement).toBe(cycle);
  });
  it("the summary follows edits", async () => {
    phone(true); await open();
    fireEvent.change(document.querySelector<HTMLInputElement>('input[name="duration"]')!, { target: { value: "45" } });
    expect(toggle(/^Manual fault injection/).textContent).toContain("F2 · 90% · HTTP 503 · 45 s");
  });
  it("a running fault locks and collapses both, with the controller's own values", async () => {
    phone(true); status = controllerStatus("manual"); await open();
    const manual = toggle(/^Manual fault injection/);
    const cycle = toggle(/^Automatic cycle/);
    expect(manual.getAttribute("aria-expanded")).toBe("false");
    expect(cycle.getAttribute("aria-expanded")).toBe("false");
    expect(manual.textContent).toMatch(/Locked · F2 · \d+% · HTTP \d+ · [\d.]+ s/);
    expect(cycle.textContent).toContain("Locked · 1 repeat · 12 min 30 s");
    expect(screen.queryByRole("button", { name: "Inject fault" })).toBeNull();
  });
  it("a running cycle summarises the controller's schedule", async () => {
    phone(true); status = controllerStatus("automatic"); await open();
    expect(toggle(/^Automatic cycle/).textContent).toContain("Locked · 3 repeats · 31 min · shuffled");
    expect(toggle(/^Manual fault injection/).getAttribute("aria-expanded")).toBe("false");
  });
  it("opening a locked section shows its lock notice and locked controls", async () => {
    phone(true); status = controllerStatus("manual"); await open();
    fireEvent.click(toggle(/^Manual fault injection/));
    expect(screen.getByText("One manual fault at a time. Stop the active fault to change parameters.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Inject fault" }).getAttribute("aria-disabled")).toBe("true");
  });
  it("on wider screens nothing collapses and there are no disclosure buttons", async () => {
    phone(false); await open();
    expect(screen.queryByRole("button", { name: /^Manual fault injection/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Automatic cycle/ })).toBeNull();
    expect(screen.getByRole("heading", { name: "Manual fault injection" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start cycle" })).toBeTruthy();
    expect(bar()).toBeNull();
  });
});

describe("sticky bottom Stop bar", () => {
  it("is absent when idle", async () => { phone(true); await open(); expect(bar()).toBeNull(); });
  it("shows the running fault, time left and a Stop that uses the same action", async () => {
    phone(true); status = controllerStatus("manual"); await open();
    const region = bar()!;
    expect(region).not.toBeNull();
    expect(within(region).getByText("Payment Errors")).toBeTruthy();
    expect(within(region).getByText(/^\d\d:\d\d left$/)).toBeTruthy();
    const stop = within(region).getByRole("button", { name: "Stop Payment Errors" });
    // The banner's own button keeps its name and is the only one with it.
    expect(screen.getAllByRole("button", { name: "Stop fault" })).toHaveLength(1);
    fireEvent.click(stop); await flush(100);
    expect(posts("/faults/deactivate")).toHaveLength(1);
    expect(posts("/faults/deactivate")[0]?.body).toEqual({ scenario: "payments_errors" });
  });
  it("stops a cycle through the cycle stop route", async () => {
    phone(true); status = controllerStatus("automatic"); await open();
    expect(screen.getAllByRole("button", { name: "Stop cycle" })).toHaveLength(1);
    fireEvent.click(within(bar()!).getByRole("button", { name: "Stop automatic cycle" })); await flush(100);
    expect(posts("/faults/auto/stop")).toHaveLength(1);
  });
  it("is never gated by freshness: an old answer still lets it stop", async () => {
    phone(true); status = controllerStatus("manual"); await open();
    focusManager.setFocused(false); await flush(20_000);
    fireEvent.click(within(bar()!).getByRole("button", { name: "Stop Payment Errors" })); await flush(100);
    expect(posts("/faults/deactivate")).toHaveLength(1);
  });
  it("follows the controller's controls: locked means aria-disabled and no POST", async () => {
    phone(true); status = controllerStatus("manual"); status.controls.deactivate = { ...status.controls.deactivate!, enabled: false }; await open();
    const stop = within(bar()!).getByRole("button", { name: "Stop Payment Errors" });
    expect(stop.getAttribute("aria-disabled")).toBe("true");
    fireEvent.click(stop); await flush(100);
    expect(posts("/faults/deactivate")).toHaveLength(0);
  });
  it("goes away once the fault is stopped and focus moves to the banner title", async () => {
    phone(true); status = controllerStatus("manual"); await open();
    const stop = within(bar()!).getByRole("button", { name: "Stop Payment Errors" });
    stop.focus(); status = controllerStatus("recovering"); fireEvent.click(stop); await flush(100);
    expect(bar()).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  });
});

describe("disclosure state across changes (review findings)", () => {
  it("a choice does not survive the lock it was made before: idle -> fault -> idle restores the defaults", async () => {
    phone(true); await open();
    fireEvent.click(toggle(/^Automatic cycle/)); // the operator opens the cycle while unlocked
    fireEvent.click(toggle(/^Manual fault injection/)); // and closes the manual form
    status = controllerStatus("manual"); await flush(5000);
    expect(toggle(/^Automatic cycle/).getAttribute("aria-expanded")).toBe("false");
    status = controllerStatus("idle"); await flush(5000);
    expect(toggle(/^Manual fault injection/).getAttribute("aria-expanded")).toBe("true");
    expect(toggle(/^Automatic cycle/).getAttribute("aria-expanded")).toBe("false");
  });
  it("a confirmed press does not take focus from a disclosure the operator moved to meanwhile", async () => {
    phone(true);
    let release!: () => void; activateAnswer = new Promise<void>((resolve) => { release = resolve; });
    await open();
    const inject = screen.getByRole("button", { name: "Inject fault" });
    inject.focus(); fireEvent.click(inject); await flush(100);
    const other = toggle(/^Automatic cycle/); other.focus();
    status = controllerStatus("manual"); release(); await flush(5000);
    expect(posts("/faults/activate")).toHaveLength(1);
    expect(document.activeElement).toBe(other);
  });
  it("a confirmed press whose section collapses under focus still hands focus to the banner title", async () => {
    phone(true); await open();
    const inject = screen.getByRole("button", { name: "Inject fault" });
    inject.focus(); status = controllerStatus("manual"); fireEvent.click(inject); await flush(5000);
    expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 }));
  });
  it("crossing into the phone layout keeps focus: a focused field in a section that collapses lands on its button", async () => {
    phone(false); await open();
    const seed = document.querySelector<HTMLInputElement>('input[name="seed"]')!;
    seed.focus(); expect(document.activeElement).toBe(seed);
    crossBreakpoint(true); await flush(50);
    expect(document.activeElement).toBe(toggle(/^Automatic cycle/));
  });
  it("crossing the breakpoint keeps what was typed", async () => {
    phone(false); await open();
    const seed = () => document.querySelector<HTMLInputElement>('input[name="seed"]')!;
    fireEvent.change(seed(), { target: { value: "99" } });
    crossBreakpoint(true); await flush(50);
    fireEvent.click(toggle(/^Automatic cycle/));
    expect(seed().value).toBe("99");
  });
});
