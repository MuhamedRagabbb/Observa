// @vitest-environment node
// P5-B: what a fault action's answer means (src/operator/manual/fault-actions.ts). A start or a
// stop counts as done only when the controller's own reply proves it: a 200 whose status shows the
// fault active (or no longer running). Everything else is either a definite refusal (nothing
// happened) or unconfirmed (it may have happened: check status, never retry blindly).
import { describe, expect, it } from "vitest";
import { controllerStatus } from "../e2e/fixtures/controller.ts";
import { interpretStart, interpretStop, type Reply } from "../src/operator/manual/fault-actions.ts";
import { parseControllerStatus } from "../src/operator/status/controller-status.ts";
import type { ActivationRequest } from "../src/operator/manual/fault-form.ts";

const request: ActivationRequest = { scenario: "payments_errors", duration_seconds: 300, parameters: { failure_rate: 0.9, status_code: 503 } };
const ok = (body: unknown): Reply => ({ status: 200, body });
const error = (status: number, error_code: string): Reply => ({ status, body: { error_code, message: "controller words", request_id: "ctl-1" } });

describe("a start", () => {
  it("is confirmed only by a 200 showing this fault active and acknowledged", () => {
    const status = controllerStatus("manual");
    expect(interpretStart(ok(status), request)).toEqual({ kind: "confirmed", status, message: "Controller confirmed the fault is active." });
  });

  it.each([
    ["a 200 that still says idle", ok(controllerStatus("idle"))],
    ["a 200 for a different scenario", ok(controllerStatus("manual", { active: { ...controllerStatus("manual").active!, scenario: "bad_deployment" } }))],
    ["a 200 whose fault is not acknowledged", ok(controllerStatus("manual", { active: { ...controllerStatus("manual").active!, status: "activation_unconfirmed", acknowledged_at: null } }))],
    ["a 200 in the unknown state", ok(controllerStatus("unknown"))],
    ["a 200 that is not a status at all", ok({ hello: "world" })],
    ["a 200 with no body", ok(null)],
  ])("is not confirmed by %s", (_name, reply) => {
    expect(interpretStart(reply, request)).toEqual({
      kind: "unconfirmed",
      message: "The controller’s answer didn’t confirm the fault. It may be running: check status before trying again.",
    });
  });

  it("503 target_unavailable: the affected service never acknowledged, so the fault may be running", () => {
    expect(interpretStart(error(503, "target_unavailable"), request)).toEqual({
      kind: "unconfirmed",
      message: "The affected service didn’t acknowledge the start. The fault may be running: check status before trying again.",
    });
  });

  it.each([
    [{ failed: "network" } as Reply, "The controller didn’t answer. The fault may or may not have started: check status before trying again."],
    [{ failed: "timeout" } as Reply, "The controller didn’t answer in time. The fault may or may not have started: check status before trying again."],
    [{ status: 500, body: "boom" } as Reply, "The controller failed while handling the start. The fault may be running: check status before trying again."],
    [{ status: 502, body: null } as Reply, "The controller failed while handling the start. The fault may be running: check status before trying again."],
  ])("an unreadable or failed answer (%j) is unconfirmed", (reply, message) => {
    expect(interpretStart(reply, request)).toEqual({ kind: "unconfirmed", message });
  });

  it.each([
    ["auto_running", "An automatic cycle is running. Stop it before starting a manual fault."],
    ["fault_conflict", "A fault is already active. Stop it before starting another."],
    ["recovering", "Services are still recovering from the last fault. Wait until the checks pass."],
    ["status_unknown", "The fault status is unknown. Check status first."],
    ["controller_busy", "The controller is busy. Try again in a moment."],
    ["something_new", "The controller refused the request (something_new)."],
  ])("409 %s is a definite refusal", (code, message) => {
    expect(interpretStart(error(409, code), request)).toEqual({ kind: "refused", code, message });
  });

  it("422 means the settings were rejected, nothing started", () => {
    expect(interpretStart(error(422, "validation_error"), request)).toEqual({
      kind: "refused",
      code: "validation_error",
      message: "The controller rejected these settings. Check the values and try again.",
    });
  });

  it("403 means this page isn’t allowed to control it, nothing started", () => {
    expect(interpretStart(error(403, "origin_not_allowed"), request)).toEqual({
      kind: "refused",
      code: "origin_not_allowed",
      message: "The controller blocked the request. Open this page from the controller’s own address.",
    });
  });

  it("a 4xx without a readable error is still a refusal", () => {
    expect(interpretStart({ status: 404, body: "not found" }, request)).toEqual({ kind: "refused", code: "http_404", message: "The controller refused the request (http_404)." });
  });
});

describe("a stop", () => {
  const stopped = controllerStatus("recovering");

  it("is confirmed by a 200 that no longer shows the fault running", () => {
    expect(interpretStop(ok(stopped), "payments_errors")).toEqual({ kind: "confirmed", status: stopped, message: "Controller confirmed the fault stopped." });
    expect(interpretStop(ok(controllerStatus("idle")), "payments_errors")).toMatchObject({ kind: "confirmed" });
  });

  it("is not confirmed while the same fault still shows as running or unknown", () => {
    const message = "The controller’s answer didn’t confirm the stop. The fault may still be running: check status.";
    expect(interpretStop(ok(controllerStatus("manual")), "payments_errors")).toEqual({ kind: "unconfirmed", message });
    expect(interpretStop(ok(controllerStatus("unknown")), "payments_errors")).toEqual({ kind: "unconfirmed", message });
    expect(interpretStop(ok({ nonsense: true }), "payments_errors")).toEqual({ kind: "unconfirmed", message });
  });

  it("a different fault is running now: nothing was stopped, and the page says so", () => {
    expect(interpretStop(ok(controllerStatus("manual")), "bad_deployment")).toEqual({
      kind: "refused",
      code: "different_fault",
      message: "A different fault is running now, so nothing was stopped. Check the current state.",
    });
  });

  it("503 target_unavailable: the affected service didn’t acknowledge the stop (the wording is about the service, not the controller)", () => {
    expect(interpretStop(error(503, "target_unavailable"), "payments_errors")).toEqual({
      kind: "unconfirmed",
      message: "The affected service didn’t acknowledge the stop. The fault may still be running: check status, then stop again.",
    });
  });

  it("409 auto_running is a definite refusal", () => {
    expect(interpretStop(error(409, "auto_running"), "payments_errors")).toEqual({
      kind: "refused",
      code: "auto_running",
      message: "An automatic cycle is running. Stop the cycle instead.",
    });
  });

  it("network, timeout and server failures are unconfirmed", () => {
    expect(interpretStop({ failed: "network" }, "payments_errors")).toEqual({
      kind: "unconfirmed",
      message: "The controller didn’t answer. The fault may still be running: check status, then stop again.",
    });
    expect(interpretStop({ failed: "timeout" }, "payments_errors")).toEqual({
      kind: "unconfirmed",
      message: "The controller didn’t answer in time. The fault may still be running: check status, then stop again.",
    });
    expect(interpretStop({ status: 500, body: null }, "payments_errors")).toMatchObject({ kind: "unconfirmed" });
  });
});

describe("the controller's permissions must be real booleans (review)", () => {
  it("a 200 whose controls are malformed does not confirm a start", () => {
    const status = controllerStatus("manual");
    const malformed = { ...status, controls: { ...status.controls, activate: { enabled: "false", reason: null } } };
    expect(interpretStart(ok(malformed), request)).toMatchObject({ kind: "unconfirmed" });
  });

  it("parseControllerStatus refuses non-boolean or non-object controls", () => {
    const status = controllerStatus("idle");
    expect(() => parseControllerStatus({ ...status, controls: { ...status.controls, activate: { enabled: "true", reason: null } } })).toThrow();
    expect(() => parseControllerStatus({ ...status, controls: { ...status.controls, deactivate: null } })).toThrow();
    expect(() => parseControllerStatus({ ...status, controls: { ...status.controls, activate: { enabled: true, reason: 5 } } })).toThrow();
    expect(parseControllerStatus(status)).toEqual(status);
    const withoutReason = { ...status, controls: { ...status.controls, activate: { enabled: true } } };
    expect(parseControllerStatus(withoutReason).controls.activate?.enabled).toBe(true);
  });
});
