// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8090/ui/next/"}
// P5-A: the Operator Dashboard reads GET /faults/status every 2.5 s while the page is visible,
// shows the controller's state in the header and the experiment banner, and turns honest when the
// controller cannot be reached (a failed, timed-out or unreadable answer). Retry connection and
// Check status only read again. Nothing on this page sends anything but GET.
import { focusManager } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OPERATOR_UI_CONFIG } from "../e2e/fixtures/gateway.ts";
import { controllerStatus, type ControllerStatus } from "../e2e/fixtures/controller.ts";
import { operatorRoutes } from "../src/operator/routes.tsx";
import { STATUS_POLL_MS, STATUS_TIMEOUT_MS } from "../src/operator/status/controller-status.ts";
import { AppProviders } from "../src/shared/AppProviders.tsx";

type Answer = ControllerStatus | "network-error" | "hang" | Response;
let answers: Answer[] = [];
const calls: { method: string; path: string }[] = [];

function stub() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const request = input instanceof Request ? input : new Request(new URL(String(input), window.location.href), init);
      const url = new URL(request.url);
      calls.push({ method: request.method, path: url.pathname });
      if (url.pathname === "/ui/config") return Response.json(OPERATOR_UI_CONFIG);
      if (url.pathname !== "/faults/status") return new Response("not in this test", { status: 404 });
      const answer = answers.length > 1 ? answers.shift() : answers[0];
      if (answer === "network-error") throw new TypeError("Failed to fetch");
      if (answer === "hang")
        return new Promise<Response>((_, reject) => request.signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
      if (answer instanceof Response) return answer;
      return Response.json(answer);
    }),
  );
}

const statusReads = () => calls.filter((c) => c.path === "/faults/status").length;
/** Advances the fake clock, then gives promises and React Query's 0 ms timers a few turns. */
const flush = async (ms = 0) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
    for (let turn = 0; turn < 5; turn++) await vi.advanceTimersByTimeAsync(1);
  });
};

async function open() {
  render(
    <AppProviders>
      <RouterProvider router={createMemoryRouter(operatorRoutes, { initialEntries: ["/"] })} />
    </AppProviders>,
  );
  await flush();
  await flush();
}

const banner = () => screen.getByRole("region", { name: "Experiment" });
const header = () => screen.getByRole("banner");

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"] });
  answers = [controllerStatus("idle")];
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

describe("header", () => {
  it("shows the wordmark, the operator label, the controller connection and the Storefront link", async () => {
    await open();
    expect(within(header()).getByRole("img", { name: "OBSERVA" })).toBeTruthy();
    expect(within(header()).getByText("Operator")).toBeTruthy();
    expect(within(header()).getByText("Controller connected")).toBeTruthy();
    expect(within(header()).getByRole("link", { name: /Storefront/ }).getAttribute("href")).toBe("http://127.0.0.1:18000/");
  });

  it("says Connecting before the first answer, and Controller disconnected when it fails", async () => {
    answers = ["hang"];
    await open();
    expect(within(header()).getByText("Connecting to controller")).toBeTruthy();
    expect(within(banner()).getByRole("heading", { level: 1, name: "Connecting" })).toBeTruthy();
    await flush(STATUS_TIMEOUT_MS);
    expect(within(header()).getByText("Controller disconnected")).toBeTruthy();
  });
});

describe("experiment banner", () => {
  it("idle", async () => {
    await open();
    const region = banner();
    expect(within(region).getByRole("heading", { level: 1, name: "Idle" })).toBeTruthy();
    expect(region.textContent).toContain("No fault is running.");
    expect(region.textContent).toContain("No active timer");
    expect(within(region).queryByRole("button")).toBeNull();
  });

  it("a manual fault counts down on the controller's clock, once a second, without announcing each tick", async () => {
    answers = [controllerStatus("manual")];
    await open();
    expect(within(banner()).getByRole("heading", { level: 1, name: "Payment Errors" })).toBeTruthy();
    expect(banner().textContent).toContain("03:42");
    expect(banner().textContent).toContain("Remaining of 5:00");
    await flush(2000);
    expect(banner().textContent).toContain("03:40");
    expect(screen.getByTestId("experiment-announcer").textContent).toBe("Manual fault active: Payment Errors.");
  });

  it("an automatic cycle shows its repeat, phase and phase countdown", async () => {
    answers = [controllerStatus("automatic")];
    await open();
    expect(banner().textContent).toMatch(/Automatic cycle · Repeat 2 of 3.*Payment Errors.*Fault phase · shuffled · seed 7341.*01:18.*Left in this phase/);
  });

  it("recovering waits for services and offers nothing to press", async () => {
    answers = [controllerStatus("recovering")];
    await open();
    expect(within(banner()).getByRole("heading", { level: 1, name: "Waiting for services" })).toBeTruthy();
    expect(within(banner()).queryByRole("button")).toBeNull();
  });

  it("unknown: Check status reads the status again (GET only) and shows the answer", async () => {
    answers = [controllerStatus("unknown")];
    await open();
    expect(within(banner()).getByRole("heading", { level: 1, name: "Unknown" })).toBeTruthy();
    const before = statusReads();
    answers = [controllerStatus("idle")];
    const check = within(banner()).getByRole("button", { name: "Check status" });
    check.focus();
    fireEvent.click(check);
    await flush();
    await flush();
    expect(statusReads()).toBe(before + 1);
    expect(within(banner()).getByRole("heading", { level: 1, name: "Idle" })).toBeTruthy();
    // The button went away with the state: focus moves to the new state's title, not the page.
    expect(document.activeElement).toBe(within(banner()).getByRole("heading", { level: 1, name: "Idle" }));
  });
});

describe("connection", () => {
  it.each([
    ["a network error", "network-error"],
    ["a 503", new Response("{}", { status: 503 })],
    ["an answer that is not a controller status", Response.json({ state: "busy", now: "later" })],
  ] as const)("%s makes it disconnected and keeps the last confirmed state", async (_name, failure) => {
    answers = [controllerStatus("manual")];
    await open();
    answers = [failure as Answer];
    await flush(STATUS_POLL_MS);
    await flush();
    expect(within(header()).getByText("Controller disconnected")).toBeTruthy();
    expect(within(banner()).getByRole("heading", { level: 1, name: "Unreachable" })).toBeTruthy();
    expect(banner().textContent).toContain("the last confirmed state was a running fault");
    // The countdown of a state it can no longer confirm is not shown.
    expect(banner().textContent).not.toMatch(/\d\d:\d\d/);
  });

  it("a request that hangs is given up after the time limit", async () => {
    await open();
    answers = ["hang"];
    await flush(STATUS_POLL_MS);
    expect(within(header()).getByText("Controller connected")).toBeTruthy();
    await flush(STATUS_TIMEOUT_MS);
    expect(within(header()).getByText("Controller disconnected")).toBeTruthy();
  });

  it("Retry connection reads again; once it answers, the page is live and focus moves to its state", async () => {
    answers = ["network-error"];
    await open();
    const retry = within(banner()).getByRole("button", { name: "Retry connection" });
    answers = [controllerStatus("idle")];
    retry.focus();
    fireEvent.click(retry);
    await flush(100); // past the live region's short delay before it speaks
    expect(within(header()).getByText("Controller connected")).toBeTruthy();
    expect(document.activeElement).toBe(within(banner()).getByRole("heading", { level: 1, name: "Idle" }));
    expect(screen.getByTestId("experiment-announcer").textContent).toBe("Idle. No fault is running.");
  });

  it("polling also brings it back by itself", async () => {
    answers = ["network-error"];
    await open();
    answers = [controllerStatus("recovering")];
    await flush(STATUS_POLL_MS);
    await flush();
    expect(within(header()).getByText("Controller connected")).toBeTruthy();
  });

  it("a cycle being stopped (state automatic, no cycle details yet) is a live answer, not a disconnect", async () => {
    answers = [controllerStatus("automatic", { auto: null, active: null })];
    await open();
    expect(within(header()).getByText("Controller connected")).toBeTruthy();
    expect(within(banner()).getByRole("heading", { level: 1, name: "Stopping the cycle" })).toBeTruthy();
  });
});

describe("focus after an action (review finding)", () => {
  it("a check that changes nothing leaves focus alone later, when a poll changes the state", async () => {
    answers = [controllerStatus("unknown")];
    await open();
    fireEvent.click(within(banner()).getByRole("button", { name: "Check status" }));
    await flush(100);
    // The operator moves on; minutes later the state changes by itself.
    const link = within(header()).getByRole("link", { name: /Storefront/ });
    link.focus();
    answers = [controllerStatus("idle")];
    await flush(STATUS_POLL_MS * 2);
    expect(within(banner()).getByRole("heading", { level: 1, name: "Idle" })).toBeTruthy();
    expect(document.activeElement).toBe(link);
  });
});

describe("polling", () => {
  it("reads every 2.5 s while the page is visible", async () => {
    await open();
    const first = statusReads();
    await flush(STATUS_POLL_MS * 4);
    expect(statusReads() - first).toBe(4);
  });

  it("stops while the page is hidden and reads at once when it is shown again", async () => {
    await open();
    await act(async () => focusManager.setFocused(false));
    const hidden = statusReads();
    await flush(STATUS_POLL_MS * 6);
    expect(statusReads()).toBe(hidden);
    await act(async () => focusManager.setFocused(true));
    await flush();
    expect(statusReads()).toBe(hidden + 1);
  });

  it("never sends anything but GET", async () => {
    answers = [controllerStatus("unknown")];
    await open();
    fireEvent.click(within(banner()).getByRole("button", { name: "Check status" }));
    await flush(STATUS_POLL_MS * 3);
    answers = ["network-error"];
    await flush(STATUS_POLL_MS);
    fireEvent.click(within(banner()).getByRole("button", { name: "Retry connection" }));
    await flush();
    expect(calls.length).toBeGreaterThan(4);
    expect(new Set(calls.map((c) => c.method))).toEqual(new Set(["GET"]));
  });
});
