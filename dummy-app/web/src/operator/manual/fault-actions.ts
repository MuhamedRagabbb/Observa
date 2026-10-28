import { createControllerClient } from "../../api/controller-client.ts";
import type { ScenarioId } from "../fault-contract.ts";
import { isRecord, parseControllerStatus, type ControllerStatus } from "../status/controller-status.ts";
import type { ActivationRequest } from "./fault-form.ts";

export type Reply = { status: number; body: unknown } | { failed: "network" | "timeout" };
export type ActionOutcome =
  | { kind: "confirmed"; status: ControllerStatus; message: string }
  | { kind: "refused"; code: string; message: string }
  | { kind: "unconfirmed"; message: string };

export const ACTION_TIMEOUT_MS = 15000;

const REFUSALS: Record<string, string> = {
  fault_conflict: "A fault is already active. Stop it before starting another.",
  fault_active: "A fault is already active. Stop it before starting another.",
  recovering: "Services are still recovering from the last fault. Wait until the checks pass.",
  status_unknown: "The fault status is unknown. Check status first.",
  controller_busy: "The controller is busy. Try again in a moment.",
};

function unconfirmed(message: string): ActionOutcome {
  return { kind: "unconfirmed", message };
}

function failure(reply: Reply, action: "start" | "stop"): ActionOutcome | null {
  const stopping = action === "stop";
  if ("failed" in reply) {
    const answer = reply.failed === "timeout" ? "The controller didn’t answer in time." : "The controller didn’t answer.";
    return unconfirmed(`${answer} ${stopping ? "The fault may still be running: check status, then stop again." : "The fault may or may not have started: check status before trying again."}`);
  }
  const code = isRecord(reply.body) && typeof reply.body.error_code === "string" && reply.body.error_code
    ? reply.body.error_code : `http_${reply.status}`;
  if (reply.status >= 400 && reply.status < 500) {
    let message = `The controller refused the request (${code}).`;
    if (reply.status === 403) message = "The controller blocked the request. Open this page from the controller’s own address.";
    else if (reply.status === 422) message = "The controller rejected these settings. Check the values and try again.";
    else if (reply.status === 409) {
      if (code === "auto_running") message = stopping ? "An automatic cycle is running. Stop the cycle instead." : "An automatic cycle is running. Stop it before starting a manual fault.";
      else if (Object.hasOwn(REFUSALS, code)) message = REFUSALS[code] ?? message;
    }
    return { kind: "refused", code, message };
  }
  if (reply.status === 503 && code === "target_unavailable") {
    return unconfirmed(stopping
      ? "The affected service didn’t acknowledge the stop. The fault may still be running: check status, then stop again."
      : "The affected service didn’t acknowledge the start. The fault may be running: check status before trying again.");
  }
  if (reply.status >= 500) {
    return unconfirmed(stopping
      ? "The controller failed while handling the stop. The fault may still be running: check status, then stop again."
      : "The controller failed while handling the start. The fault may be running: check status before trying again.");
  }
  return null;
}

function confirmedStatus(reply: Reply): ControllerStatus | null {
  if ("failed" in reply || reply.status !== 200) return null;
  try {
    return parseControllerStatus(reply.body);
  } catch {
    return null;
  }
}

export function interpretStart(reply: Reply, request: ActivationRequest): ActionOutcome {
  const failed = failure(reply, "start");
  if (failed) return failed;
  const status = confirmedStatus(reply);
  const active = status?.active;
  if (status?.state === "manual" && !status.auto_running && active?.scenario === request.scenario && active.status === "active" && typeof active.acknowledged_at === "string" && Number.isFinite(Date.parse(active.acknowledged_at))) {
    return { kind: "confirmed", status, message: "Controller confirmed the fault is active." };
  }
  return unconfirmed("The controller’s answer didn’t confirm the fault. It may be running: check status before trying again.");
}

export function interpretStop(reply: Reply, scenario: ScenarioId): ActionOutcome {
  const failed = failure(reply, "stop");
  if (failed) return failed;
  const status = confirmedStatus(reply);
  if (status && (status.state === "idle" || status.state === "recovering") && status.active === null && !status.auto_running) {
    return { kind: "confirmed", status, message: "Controller confirmed the fault stopped." };
  }
  if (status?.state === "manual" && status.active && status.active.scenario !== scenario) {
    return { kind: "refused", code: "different_fault", message: "A different fault is running now, so nothing was stopped. Check the current state." };
  }
  return unconfirmed("The controller’s answer didn’t confirm the stop. The fault may still be running: check status.");
}

async function post(
  send: (signal: AbortSignal) => Promise<{ data?: unknown; error?: unknown; response: Response }>,
  signal?: AbortSignal,
): Promise<Reply> {
  const limit = new AbortController();
  const onAbort = () => limit.abort();
  if (signal?.aborted) limit.abort();
  signal?.addEventListener("abort", onAbort);
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    limit.abort();
  }, ACTION_TIMEOUT_MS);
  try {
    const result = await send(limit.signal);
    let body: unknown = result.response.ok ? result.data : result.error;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body) as unknown;
      } catch {
        // Keep unreadable text as an answer, rather than calling it a network failure.
      }
    }
    return { status: result.response.status, body };
  } catch {
    return { failed: timedOut ? "timeout" : "network" };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

export async function postStart(request: ActivationRequest, signal?: AbortSignal): Promise<ActionOutcome> {
  const reply = await post((signal) => createControllerClient().POST("/faults/activate", { body: request, parseAs: "text", credentials: "omit", signal }), signal);
  return interpretStart(reply, request);
}

export async function postStop(scenario: ScenarioId, signal?: AbortSignal): Promise<ActionOutcome> {
  const reply = await post((signal) => createControllerClient().POST("/faults/deactivate", { body: { scenario }, parseAs: "text", credentials: "omit", signal }), signal);
  return interpretStop(reply, scenario);
}
