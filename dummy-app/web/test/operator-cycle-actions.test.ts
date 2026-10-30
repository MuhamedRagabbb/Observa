// @vitest-environment node
// P5-C: what a cycle start or stop answer means (src/operator/cycle/cycle-actions.ts). A cycle
// counts as started only when the controller's own 200 shows an automatic state running THIS
// configuration; as stopped only when it shows no cycle and no fault running. Anything else is a
// definite refusal (nothing happened) or unconfirmed (check status, never retry blindly).
import { describe, expect, it } from "vitest";
import { controllerStatus } from "../e2e/fixtures/controller.ts";
import { interpretCycleStart, interpretCycleStop } from "../src/operator/cycle/cycle-actions.ts";
import type { CycleRequest } from "../src/operator/cycle/cycle-form.ts";
import type { Reply } from "../src/operator/manual/fault-actions.ts";

// The automatic fixture runs: seed 7341, shuffled, F1 + F2, warmup 60, fault 120, recovery 180, 3 repeats.
const request: CycleRequest = {
  seed: 7341,
  scenarios: ["db_connection_exhaustion", "payments_errors"],
  shuffle: true,
  warmup_seconds: 60,
  duration_seconds: 120,
  recovery_seconds: 180,
  repeat: 3,
};
const ok = (body: unknown): Reply => ({ status: 200, body });
const error = (status: number, error_code: string): Reply => ({ status, body: { error_code, message: "controller words", request_id: "ctl-1" } });
const UNCONFIRMED_START = "The controller’s answer didn’t confirm the cycle. It may be running: check status before trying again.";

describe("a cycle start", () => {
  it("is confirmed only by a 200 showing an automatic state running this very configuration", () => {
    const status = controllerStatus("automatic");
    expect(interpretCycleStart(ok(status), request)).toEqual({ kind: "confirmed", status, message: "Controller confirmed the cycle is running." });
  });

  it.each([
    ["a 200 that still says idle", ok(controllerStatus("idle"))],
    ["a 200 in the unknown state", ok(controllerStatus("unknown"))],
    ["a 200 for a manual fault", ok(controllerStatus("manual"))],
    ["a 200 that is not a status", ok({ hello: "world" })],
    ["a 200 with no body", ok(null)],
    ["a 200 with no cycle details", ok(controllerStatus("automatic", { auto: null }))],
  ])("is not confirmed by %s", (_name, reply) => {
    expect(interpretCycleStart(reply, request)).toEqual({ kind: "unconfirmed", message: UNCONFIRMED_START });
  });

  it.each([
    ["a different seed", { seed: 1 }],
    ["a different order setting", { shuffle: false }],
    ["different scenarios", { scenarios: ["payments_errors"] as CycleRequest["scenarios"] }],
    ["a different warmup", { warmup_seconds: 61 }],
    ["a different fault duration", { duration_seconds: 119 }],
    ["a different recovery", { recovery_seconds: 0 }],
    ["a different repeat count", { repeat: 4 }],
  ])("is not confirmed when the running cycle has %s", (_name, change) => {
    expect(interpretCycleStart(ok(controllerStatus("automatic")), { ...request, ...change })).toEqual({ kind: "unconfirmed", message: UNCONFIRMED_START });
  });

  it.each([
    [{ failed: "network" } as Reply, "The controller didn’t answer. The cycle may or may not have started: check status before trying again."],
    [{ failed: "timeout" } as Reply, "The controller didn’t answer in time. The cycle may or may not have started: check status before trying again."],
    [{ status: 500, body: "boom" } as Reply, "The controller failed while handling the start. The cycle may be running: check status before trying again."],
    [error(503, "target_unavailable"), "The controller failed while handling the start. The cycle may be running: check status before trying again."],
  ])("an unreadable or failed answer (%j) is unconfirmed", (reply, message) => {
    expect(interpretCycleStart(reply, request)).toEqual({ kind: "unconfirmed", message });
  });

  it.each([
    ["controller_busy", "A cycle or a fault is already running. Stop it before starting a cycle."],
    ["auto_running", "A cycle is already running."],
    ["recovering", "Services are still recovering from the last fault. Wait until the checks pass."],
    ["status_unknown", "The fault status is unknown. Check status first."],
    ["something_new", "The controller refused the request (something_new)."],
  ])("409 %s is a definite refusal", (code, message) => {
    expect(interpretCycleStart(error(409, code), request)).toEqual({ kind: "refused", code, message });
  });

  it("422 means the settings were rejected, nothing started", () => {
    expect(interpretCycleStart(error(422, "validation_error"), request)).toEqual({
      kind: "refused",
      code: "validation_error",
      message: "The controller rejected these settings. Check the values and try again.",
    });
  });

  it("403 means this page isn’t allowed to control it", () => {
    expect(interpretCycleStart(error(403, "origin_not_allowed"), request)).toEqual({
      kind: "refused",
      code: "origin_not_allowed",
      message: "The controller blocked the request. Open this page from the controller’s own address.",
    });
  });
});

describe("a cycle stop", () => {
  it("is confirmed by a 200 showing no cycle and no fault running", () => {
    const stopped = controllerStatus("recovering");
    expect(interpretCycleStop(ok(stopped))).toEqual({ kind: "confirmed", status: stopped, message: "Controller confirmed the cycle stopped." });
    expect(interpretCycleStop(ok(controllerStatus("idle")))).toMatchObject({ kind: "confirmed" });
  });

  it.each([
    ["automatic: the cycle is still running", ok(controllerStatus("automatic"))],
    ["unknown: a fault it started was not confirmed stopped", ok(controllerStatus("unknown"))],
    ["a manual fault", ok(controllerStatus("manual"))],
    ["not a status", ok({ nonsense: true })],
  ])("is not confirmed while the answer shows %s", (_name, reply) => {
    expect(interpretCycleStop(reply)).toEqual({
      kind: "unconfirmed",
      message: "The controller’s answer didn’t confirm the stop. The cycle or a fault may still be running: check status.",
    });
  });

  it("503 target_unavailable: the affected service didn’t acknowledge stopping the fault the cycle had started", () => {
    expect(interpretCycleStop(error(503, "target_unavailable"))).toEqual({
      kind: "unconfirmed",
      message: "The cycle was told to stop, but the affected service didn’t acknowledge stopping its fault. The fault may still be running: check status, then use Stop again.",
    });
  });

  it("network, timeout and server failures are unconfirmed", () => {
    expect(interpretCycleStop({ failed: "network" })).toEqual({
      kind: "unconfirmed",
      message: "The controller didn’t answer. The cycle may still be running: check status, then stop again.",
    });
    expect(interpretCycleStop({ failed: "timeout" })).toEqual({
      kind: "unconfirmed",
      message: "The controller didn’t answer in time. The cycle may still be running: check status, then stop again.",
    });
    expect(interpretCycleStop({ status: 500, body: null })).toMatchObject({ kind: "unconfirmed" });
  });

  it("a refusal (403) is definite", () => {
    expect(interpretCycleStop(error(403, "cross_site"))).toMatchObject({ kind: "refused", code: "cross_site" });
  });
});
