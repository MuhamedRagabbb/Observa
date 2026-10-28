// @vitest-environment node
// P5-D: the Session activity list (src/operator/activity/session-activity.ts). The controller's
// /activity log is in memory and has no ids: this session shows entries from the moment the page
// opened (on the controller's clock), newest first, once each, in plain English.
import { describe, expect, it } from "vitest";
import { activity, activityEntry, CONTROLLER_NOW } from "../e2e/fixtures/controller.ts";
import { describeEntry, sessionActivity } from "../src/operator/activity/session-activity.ts";

// activityEntry(event, s) is at CONTROLLER_NOW - 120 + s.
const entry = (event: string, seconds: number, extras: Record<string, unknown> = {}) => activityEntry(event, seconds, extras);
const list = (entries: ReturnType<typeof entry>[], sessionStart = CONTROLLER_NOW - 120) => sessionActivity({ entries: activity(entries).entries, sessionStart, timeZone: "UTC" });

describe("which entries belong to this session", () => {
  it("keeps entries at or after the page-open time and drops earlier ones", () => {
    const items = list([entry("fault_requested", -1, { scenario: "payments_errors" }), entry("fault_active", 0, { scenario: "payments_errors" }), entry("fault_stopped", 5, { scenario: "payments_errors", reason: "manual" })]);
    expect(items.map((item) => item.text)).toEqual(["Fault stopped · F2 Payment Errors · stopped manually", "Controller confirmed fault is active"]);
  });

  it("lists newest first even when the entries arrive oldest first", () => {
    const items = sessionActivity({ entries: [entry("fault_active", 1, { scenario: "payments_errors" }), entry("stop_requested", 9, { scenario: "payments_errors" })], sessionStart: CONTROLLER_NOW - 120, timeZone: "UTC" });
    expect(items.map((item) => item.time)).toEqual(["13:58:09", "13:58:01"]);
  });

  it("shows an entry once when the same time, event and content comes twice (repeated polls)", () => {
    const same = entry("fault_active", 1, { scenario: "payments_errors" });
    expect(list([same, { ...same }])).toHaveLength(1);
  });

  it("keeps two entries that share a time and event but differ in content", () => {
    const items = list([entry("service_health_changed", 3, { service: "orders-service", previous: "healthy", state: "degraded" }), entry("service_health_changed", 3, { service: "payments-service", previous: "healthy", state: "degraded" })]);
    expect(items).toHaveLength(2);
    expect(new Set(items.map((item) => item.key)).size).toBe(2);
  });

  it("ignores an entry whose time cannot be read", () => {
    expect(list([{ at: "garbage", event: "fault_active" }])).toEqual([]);
  });

  it("is empty when nothing has happened since the page opened", () => {
    expect(list([entry("fault_active", -30)])).toEqual([]);
  });
});

describe("event copy", () => {
  const text = (event: string, extras: Record<string, unknown> = {}) => describeEntry(entry(event, 0, extras));

  it("fault_requested names the fault, its settings and its duration", () => {
    expect(text("fault_requested", { scenario: "payments_errors", duration_seconds: 300, parameters: { failure_rate: 0.9, status_code: 503 } })).toBe("Fault requested · F2 Payment Errors · 90% · HTTP 503 · 300 s");
    expect(text("fault_requested", { scenario: "db_connection_exhaustion", duration_seconds: 120, parameters: { hold_seconds: 8 } })).toBe("Fault requested · F1 Database Exhaustion · 8 s hold · 120 s");
    expect(text("fault_requested", { scenario: "bad_deployment", duration_seconds: 90, parameters: { delay_seconds: 1.5 } })).toBe("Fault requested · F3 Bad Deployment · 1.5 s delay · 90 s");
  });

  it("fault_requested without settings uses the controller's defaults", () => {
    expect(text("fault_requested", { scenario: "payments_errors", duration_seconds: 120, parameters: {} })).toBe("Fault requested · F2 Payment Errors · 90% · HTTP 503 · 120 s");
  });

  it.each([
    ["fault_active", { scenario: "payments_errors" }, "Controller confirmed fault is active"],
    ["activation_unconfirmed", { scenario: "payments_errors" }, "Fault start not confirmed · F2 Payment Errors"],
    ["stop_requested", { scenario: "payments_errors", reason: "manual" }, "Stop requested · F2 Payment Errors"],
    ["fault_stopped", { scenario: "bad_deployment", reason: "target_lease_expired" }, "Fault stopped · F3 Bad Deployment · lease expired"],
    ["fault_stopped", { scenario: "bad_deployment", reason: "auto_stopped" }, "Fault stopped · F3 Bad Deployment · cycle stopped"],
    ["fault_stopped", { scenario: "bad_deployment", reason: "ttl" }, "Fault stopped · F3 Bad Deployment · time ran out"],
    ["fault_stopped", { scenario: "bad_deployment", reason: "schedule" }, "Fault stopped · F3 Bad Deployment · cycle step ended"],
    ["fault_stopped", { scenario: "bad_deployment", reason: "controller_restart" }, "Fault stopped · F3 Bad Deployment · controller restarted"],
    ["fault_stopped", { scenario: "bad_deployment", reason: "odd_new_reason" }, "Fault stopped · F3 Bad Deployment · odd new reason"],
    ["stop_unconfirmed", { scenario: "payments_errors" }, "No confirmation received from controller"],
    ["cycle_started", { repeats: 3, seed: 7341, shuffle: true, scenarios: ["db_connection_exhaustion", "payments_errors"] }, "Cycle started · 3 repeats · F1, F2 · shuffled · seed 7341"],
    ["cycle_started", { repeats: 1, seed: 5, shuffle: false, scenarios: ["bad_deployment"] }, "Cycle started · 1 repeat · F3 · in order"],
    ["phase_started", { phase: "warmup", repeat: 1, step: 0, scenario: null }, "Warmup phase started"],
    ["phase_started", { phase: "fault", repeat: 2, step: 2, scenario: "payments_errors" }, "Fault phase started · repeat 2 · F2 Payment Errors"],
    ["phase_started", { phase: "recovery", repeat: 2, step: 2, scenario: "payments_errors" }, "Recovery phase started · repeat 2 · F2 Payment Errors"],
    ["cycle_stop_requested", {}, "Cycle stop requested"],
    ["cycle_stopped", {}, "Cycle stopped"],
    ["cycle_finished", {}, "Cycle finished"],
    ["cycle_failed", {}, "Cycle failed"],
    ["service_health_changed", { service: "orders-service", previous: "healthy", state: "degraded" }, "orders-service · Healthy → Degraded"],
    ["service_health_changed", { service: "orders-service", previous: null, state: "unknown" }, "orders-service · now Unknown"],
    ["services_recovered", { scenario: "payments_errors" }, "Services recovered after F2 Payment Errors"],
    ["controller_started", { interrupted_incidents: 0 }, "Controller started"],
    ["controller_started", { interrupted_incidents: 2 }, "Controller started · 2 interrupted faults cleaned up"],
    ["control_rejected", { action: "activate", error_code: "fault_active" }, "Controller refused the request · activate · fault active"],
  ])("%s reads naturally", (event, extras, expected) => {
    expect(text(event, extras as Record<string, unknown>)).toBe(expected);
  });

  it("an event this page does not know is shown by its name, never hidden", () => {
    expect(text("schedule_failed", { detail: "x" })).toBe("Schedule failed");
  });

  it("a known event with missing or odd fields still reads, never throws", () => {
    expect(text("fault_requested", {})).toBe("Fault requested");
    expect(text("fault_requested", { scenario: "not_a_scenario", duration_seconds: "x", parameters: null })).toBe("Fault requested · not a scenario");
    expect(text("service_health_changed", {})).toBe("Service health changed");
  });
});
