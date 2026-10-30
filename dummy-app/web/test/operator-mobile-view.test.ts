import { describe, expect, it } from "vitest";
import { controllerStatus } from "../e2e/fixtures/controller.ts";
import { defaultCycleForm } from "../src/operator/cycle/cycle-form.ts";
import { defaultForm } from "../src/operator/manual/fault-form.ts";
import { sectionOpen } from "../src/operator/mobile/disclosure.ts";
import { stopBar } from "../src/operator/mobile/stop-bar.ts";
import { cycleSummary, lockedSummary, manualSummary } from "../src/operator/mobile/summaries.ts";
import { experimentView } from "../src/operator/status/experiment.ts";

const view = (state: Parameters<typeof controllerStatus>[0], overrides: Parameters<typeof controllerStatus>[1] = {}) =>
  experimentView({ connection: "connected", status: controllerStatus(state, overrides), elapsed: 0 });

describe("manualSummary (the collapsed Manual fault injection line)", () => {
  it("names the scenario code, its settings and the duration from the form values", () => {
    expect(manualSummary({ ...defaultForm("payments_errors"), duration: "300" })).toBe("F2 · 90% · HTTP 503 · 300 s");
    expect(manualSummary({ ...defaultForm("payments_errors"), settings: { failure_rate: "35", status_code: "402" }, duration: "1.5" })).toBe("F2 · 35% · HTTP 402 · 1.5 s");
    expect(manualSummary({ ...defaultForm("db_connection_exhaustion"), duration: "120" })).toMatch(/^F1 · [\d.]+ s hold · 120 s$/);
    expect(manualSummary({ ...defaultForm("bad_deployment"), duration: "60" })).toMatch(/^F3 · [\d.]+ s delay · 60 s$/);
  });
  it("never summarises values the form would refuse", () => {
    expect(manualSummary({ ...defaultForm("payments_errors"), settings: { failure_rate: "abc", status_code: "503" } })).toBe("F2 · settings need attention");
    expect(manualSummary({ ...defaultForm("payments_errors"), duration: "0" })).toBe("F2 · settings need attention");
  });
});

describe("cycleSummary (the collapsed Automatic cycle line)", () => {
  it("counts repeats and totals the real schedule", () => {
    expect(cycleSummary(defaultCycleForm())).toBe("1 repeat · 12 min 30 s");
    expect(cycleSummary({ ...defaultCycleForm(), warmup: "60", duration: "120", recovery: "180", repeat: "3", scenarios: ["db_connection_exhaustion", "payments_errors"], shuffle: true }))
      .toBe("3 repeats · 31 min · shuffled");
  });
  it("says what is missing instead of a wrong total", () => {
    expect(cycleSummary({ ...defaultCycleForm(), scenarios: [] })).toBe("No scenario selected");
    expect(cycleSummary({ ...defaultCycleForm(), repeat: "x" })).toBe("Settings need attention");
    expect(cycleSummary({ ...defaultCycleForm(), warmup: "-1" })).toBe("Settings need attention");
  });
});

describe("lockedSummary", () => {
  it("prefixes only a locked section", () => {
    expect(lockedSummary("1 repeat · 12 min 30 s", true)).toBe("Locked · 1 repeat · 12 min 30 s");
    expect(lockedSummary("1 repeat · 12 min 30 s", false)).toBe("1 repeat · 12 min 30 s");
  });
});

describe("sectionOpen (disclosure state on phones)", () => {
  it("follows the default until the operator chooses", () => {
    expect(sectionOpen(null, false, true)).toBe(true);
    expect(sectionOpen(null, true, false)).toBe(false);
  });
  it("keeps the operator's choice while the lock does not change", () => {
    expect(sectionOpen({ open: false, locked: false }, false, true)).toBe(false);
    expect(sectionOpen({ open: true, locked: true }, true, false)).toBe(true);
  });
  it("forgets the choice when the lock changes, so the default applies again", () => {
    expect(sectionOpen({ open: true, locked: false }, true, false)).toBe(false);
    expect(sectionOpen({ open: false, locked: true }, false, true)).toBe(true);
  });
});

describe("stopBar (the sticky bottom bar on phones)", () => {
  it("shows the fault with its time left and the banner's own Stop", () => {
    const banner = view("manual");
    const bar = stopBar(banner);
    expect(bar).not.toBeNull();
    expect(bar!.title).toBe("Payment Errors");
    expect(bar!.detail).toBe(`${banner.timer!.text} left`);
    expect(bar!.stop).toEqual(banner.stop);
    expect(bar!.label).toBe("Stop Payment Errors");
  });
  it("shows the cycle phase and a label that differs from the banner's Stop cycle", () => {
    const banner = view("automatic");
    const bar = stopBar(banner)!;
    expect(bar.title).toBe(`Cycle · ${banner.title}`);
    expect(bar.detail).toBe(`${banner.timer!.text} left`);
    expect(bar.stop.kind).toBe("stop-cycle");
    expect(bar.label).toBe("Stop automatic cycle");
    expect(bar.label).not.toContain("Stop cycle");
  });
  it("says so when no countdown runs, and does not repeat a stopping cycle's title", () => {
    const starting = controllerStatus("manual");
    expect(stopBar(view("manual", { active: { ...starting.active!, status: "activating", acknowledged_at: null } }))!.detail).toBe("Not started yet");
    const stopping = stopBar(view("automatic", { auto: null }))!;
    expect(stopping.title).toBe("Stopping the cycle");
    expect(stopping.detail).toBe("No active timer");
  });
  it("keeps the bar's Stop as available as the banner's, never more", () => {
    const status = controllerStatus("manual");
    status.controls.deactivate = { ...status.controls.deactivate!, enabled: false };
    expect(stopBar(experimentView({ connection: "connected", status, elapsed: 0 }))!.stop.enabled).toBe(false);
    // Staleness never disables a stop: a long time after its answer the controller still has the say.
    expect(stopBar(experimentView({ connection: "connected", status: controllerStatus("manual"), elapsed: 600 }))!.stop.enabled).toBe(true);
  });
  it.each(["idle", "recovering", "unknown"] as const)("has no bar in the %s state", (state) => {
    expect(stopBar(view(state))).toBeNull();
  });
  it("has no bar while connecting or disconnected", () => {
    expect(stopBar(experimentView({ connection: "connecting", status: null, elapsed: 0 }))).toBeNull();
    expect(stopBar(experimentView({ connection: "disconnected", status: controllerStatus("manual"), elapsed: 0 }))).toBeNull();
  });
});
