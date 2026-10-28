// @vitest-environment node
// P5-A: what the experiment banner says for each controller state (src/operator/status/
// experiment.ts). Everything comes from the controller's answer and how long ago it arrived;
// the page never infers a running fault itself, and a countdown never runs past its end.
import { describe, expect, it } from "vitest";
import { CONTROLLER_NOW, controllerStatus } from "../e2e/fixtures/controller.ts";
import { experimentView, formatClock } from "../src/operator/status/experiment.ts";

const view = (status: ReturnType<typeof controllerStatus> | null, elapsed = 0, connection: "connecting" | "connected" | "disconnected" = "connected") =>
  experimentView({ connection, status, elapsed, timeZone: "UTC" });

describe("formatClock", () => {
  it.each([
    [222, "03:42"],
    [0, "00:00"],
    [59.2, "01:00"],
    [1800, "30:00"],
    [-5, "00:00"],
    [3725, "1:02:05"],
  ])("%s s reads %s", (seconds, text) => {
    expect(formatClock(seconds)).toBe(text);
  });
});

describe("experiment banner", () => {
  it("idle: no fault, no timer, nothing to do", () => {
    expect(view(controllerStatus("idle"))).toMatchObject({
      kind: "idle",
      eyebrow: "Experiment",
      title: "Idle",
      description: "No fault is running.",
      timer: null,
      timerNote: "No active timer",
      alert: false,
      action: null,
    });
  });

  it("manual fault: scenario, start time and settings; a countdown on the controller's clock", () => {
    const banner = view(controllerStatus("manual"));
    expect(banner).toMatchObject({
      kind: "manual",
      eyebrow: "Manual fault · F2",
      title: "Payment Errors",
      description: "Started 13:58:42 · 90% of payment requests fail with HTTP 503.",
      timer: { text: "03:42", caption: "Remaining of 5:00" },
      alert: true,
    });
    expect(banner.timer?.progress).toBeCloseTo(78 / 300, 3);
  });

  it("counts down from when the answer arrived, and stops at zero", () => {
    expect(view(controllerStatus("manual"), 2).timer?.text).toBe("03:40");
    expect(view(controllerStatus("manual"), 500).timer).toMatchObject({ text: "00:00", progress: 1 });
  });

  it.each([
    ["db_connection_exhaustion", { hold_seconds: 8 }, "Manual fault · F1", "Database Exhaustion", "Orders database connections are held for 8 s each."],
    ["bad_deployment", { delay_seconds: 1.5 }, "Manual fault · F3", "Bad Deployment", "Orders run on release v2-buggy with 1.5 s added delay."],
    ["payments_errors", { failure_rate: 0.25, status_code: 402 }, "Manual fault · F2", "Payment Errors", "25% of payment requests fail with HTTP 402."],
    // A setting left out is the controller's default (fault-parameters.json): said as such.
    ["payments_errors", {}, "Manual fault · F2", "Payment Errors", "90% of payment requests fail with HTTP 503."],
  ] as const)("%s %j", (scenario, parameters, eyebrow, title, summary) => {
    const base = controllerStatus("manual");
    const banner = view(controllerStatus("manual", { active: { ...base.active!, scenario, parameters } }));
    expect(banner).toMatchObject({ eyebrow, title });
    expect(banner.description).toBe(`Started 13:58:42 · ${summary}`);
  });

  it("automatic cycle: repeat, phase, order and seed; the phase's own countdown", () => {
    const banner = view(controllerStatus("automatic"));
    expect(banner).toMatchObject({
      kind: "automatic",
      eyebrow: "Automatic cycle · Repeat 2 of 3",
      title: "Payment Errors",
      description: "Fault phase · shuffled · seed 7341",
      timer: { text: "01:18", caption: "Left in this phase" },
      alert: true,
      action: null,
    });
    expect(banner.timer?.progress).toBeCloseTo(42 / 120, 3);
  });

  it.each([
    ["warmup", "Warmup", "Warmup before the first fault · shuffled · seed 7341"],
    ["recovery", "Recovery", "Recovery after Payment Errors · shuffled · seed 7341"],
  ] as const)("automatic cycle in its %s phase", (phase, title, description) => {
    const base = controllerStatus("automatic");
    expect(view(controllerStatus("automatic", { active: null, auto: { ...base.auto!, phase } }))).toMatchObject({ title, description });
  });

  it("a cycle that is stopping has no phase end, so no countdown", () => {
    const base = controllerStatus("automatic");
    const banner = view(controllerStatus("automatic", { auto: { ...base.auto!, phase: "stopping", phase_ends_at: null, config: { ...base.auto!.config, shuffle: false } } }));
    expect(banner).toMatchObject({ title: "Stopping the cycle", timer: null, timerNote: "No active timer" });
  });

  it("recovering: when the fault stopped, and that controls unlock when health checks pass", () => {
    expect(view(controllerStatus("recovering"))).toMatchObject({
      kind: "recovering",
      eyebrow: "Recovering",
      title: "Waiting for services",
      description: "Fault stopped at 13:59:51. Controls unlock when health checks pass.",
      timer: null,
      alert: false,
    });
  });

  it("unknown: the stop or start was not confirmed; never a countdown; Check status", () => {
    expect(view(controllerStatus("unknown"))).toMatchObject({
      kind: "unknown",
      eyebrow: "Fault status",
      title: "Unknown",
      description: "The stop request wasn’t confirmed. The fault may still be running.",
      timer: null,
      timerNote: "Unavailable",
      alert: true,
      action: "check-status",
    });
    const base = controllerStatus("unknown");
    expect(view(controllerStatus("unknown", { active: { ...base.active!, status: "activation_unconfirmed" } })).description).toBe(
      "The start request wasn’t confirmed. The fault may be running.",
    );
  });

  it.each([
    ["idle", "Can’t reach the local controller. Live state is unknown; the last confirmed state was idle."],
    ["manual", "Can’t reach the local controller. Live state is unknown; the last confirmed state was a running fault."],
    ["automatic", "Can’t reach the local controller. Live state is unknown; the last confirmed state was a running automatic cycle."],
    ["recovering", "Can’t reach the local controller. Live state is unknown; the last confirmed state was recovering after a fault."],
    ["unknown", "Can’t reach the local controller. Live state is unknown; the last confirmed state was an unconfirmed fault status."],
  ] as const)("disconnected after %s: says the last confirmed state, never counts it down", (state, description) => {
    expect(view(controllerStatus(state), 3, "disconnected")).toMatchObject({
      kind: "disconnected",
      eyebrow: "Controller",
      title: "Unreachable",
      description,
      timer: null,
      timerNote: "Unavailable",
      alert: true,
      action: "retry",
    });
  });

  it("disconnected before any answer: nothing confirmed yet", () => {
    expect(view(null, 0, "disconnected").description).toBe("Can’t reach the local controller. Live state is unknown; nothing has been confirmed yet.");
  });

  it("connecting: before the first answer, says so and claims nothing", () => {
    expect(view(null, 0, "connecting")).toMatchObject({ kind: "connecting", title: "Connecting", timer: null, timerNote: "Unavailable", action: null, alert: false });
  });

  it("each state has a short announcement for screen readers, without the countdown", () => {
    expect(view(controllerStatus("manual")).announcement).toBe("Manual fault active: Payment Errors.");
    expect(view(controllerStatus("idle")).announcement).toBe("Idle. No fault is running.");
    expect(view(controllerStatus("manual"), 0, "disconnected").announcement).toBe("Controller unreachable.");
    expect(view(controllerStatus("manual"), 5).announcement).toBe(view(controllerStatus("manual"), 0).announcement);
  });

  it("uses the controller's clock, not the browser's", () => {
    // The fixture's server time is CONTROLLER_NOW; the browser clock is irrelevant.
    expect(CONTROLLER_NOW).toBeGreaterThan(0);
    expect(view(controllerStatus("manual", { now: CONTROLLER_NOW + 100 })).timer?.text).toBe("02:02");
  });
});

describe("review findings (P5-A)", () => {
  it("a cycle being stopped before its details are known (state automatic, auto null) says so, never Idle", () => {
    // The controller answers this while stop_auto() waits for a target (app.py status/stop_auto).
    expect(view(controllerStatus("automatic", { auto: null, active: null }))).toMatchObject({
      kind: "automatic",
      eyebrow: "Automatic cycle",
      title: "Stopping the cycle",
      timer: null,
      timerNote: "No active timer",
      alert: true,
    });
  });

  it("a start time it cannot read gives no span, never NaN", () => {
    const base = controllerStatus("manual");
    const banner = view(controllerStatus("manual", { active: { ...base.active!, started_at: "not a time" } }));
    expect(banner.description).toBe("90% of payment requests fail with HTTP 503.");
    expect(banner.timer?.caption).toBe("Remaining");
    expect(JSON.stringify(banner)).not.toMatch(/NaN/);
  });

  it("a scenario this page does not know is named as sent, without settings it cannot describe", () => {
    const base = controllerStatus("manual");
    const banner = view(controllerStatus("manual", { active: { ...base.active!, scenario: "disk_full" as never } }));
    expect(banner).toMatchObject({ eyebrow: "Manual fault", title: "disk_full", description: "Started 13:58:42." });
    const auto = controllerStatus("automatic");
    expect(view(controllerStatus("automatic", { auto: { ...auto.auto!, scenario: "disk_full" as never } })).title).toBe("disk_full");
  });
});
