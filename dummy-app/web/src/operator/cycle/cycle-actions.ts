import { createControllerClient } from "../../api/controller-client.ts";
import { post, type Reply, type ActionOutcome } from "../manual/fault-actions.ts";
import { isRecord, parseControllerStatus, type ControllerStatus } from "../status/controller-status.ts";
import type { CycleRequest } from "./cycle-form.ts";

const REFUSALS: Record<string, string> = {
  controller_busy: "A cycle or a fault is already running. Stop it before starting a cycle.",
  auto_running: "A cycle is already running.",
  recovering: "Services are still recovering from the last fault. Wait until the checks pass.",
  status_unknown: "The fault status is unknown. Check status first.",
};

function unconfirmed(message: string): ActionOutcome {
  return { kind: "unconfirmed", message };
}

function failure(reply: Reply, action: "start" | "stop"): ActionOutcome | null {
  const stopping = action === "stop";
  if ("failed" in reply) {
    const answer = reply.failed === "timeout" ? "The controller didn’t answer in time." : "The controller didn’t answer.";
    return unconfirmed(`${answer} ${stopping ? "The cycle may still be running: check status, then stop again." : "The cycle may or may not have started: check status before trying again."}`);
  }
  const code = isRecord(reply.body) && typeof reply.body.error_code === "string" && reply.body.error_code
    ? reply.body.error_code : `http_${reply.status}`;
  if (reply.status >= 400 && reply.status < 500) {
    let message = `The controller refused the request (${code}).`;
    if (reply.status === 403) message = "The controller blocked the request. Open this page from the controller’s own address.";
    else if (reply.status === 422) message = "The controller rejected these settings. Check the values and try again.";
    else if (reply.status === 409 && Object.hasOwn(REFUSALS, code)) message = REFUSALS[code] ?? message;
    return { kind: "refused", code, message };
  }
  if (stopping && reply.status === 503 && code === "target_unavailable") {
    return unconfirmed("The cycle was told to stop, but the affected service didn’t acknowledge stopping its fault. The fault may still be running: check status, then use Stop again.");
  }
  if (reply.status >= 500) {
    return unconfirmed(stopping
      ? "The controller failed while handling the stop. The cycle may still be running: check status, then stop again."
      : "The controller failed while handling the start. The cycle may be running: check status before trying again.");
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

function matchesRequest(config: unknown, request: CycleRequest): boolean {
  // Confirmation requires the explicit echo, not defaults filled in for missing fields.
  if (!isRecord(config)) return false;
  const scenarios: unknown = config.scenarios;
  return (["seed", "shuffle", "warmup_seconds", "duration_seconds", "recovery_seconds", "repeat"] as const).every((key) => config[key] === request[key])
    && Array.isArray(scenarios)
    && scenarios.length === request.scenarios.length
    && request.scenarios.every((scenario, index) => scenarios[index] === scenario);
}

export function interpretCycleStart(reply: Reply, request: CycleRequest): ActionOutcome {
  const failed = failure(reply, "start");
  if (failed) return failed;
  const status = confirmedStatus(reply);
  if (status?.state === "automatic" && status.auto_running && status.auto && matchesRequest(status.auto.config, request)) {
    return { kind: "confirmed", status, message: "Controller confirmed the cycle is running." };
  }
  return unconfirmed("The controller’s answer didn’t confirm the cycle. It may be running: check status before trying again.");
}

export function interpretCycleStop(reply: Reply): ActionOutcome {
  const failed = failure(reply, "stop");
  if (failed) return failed;
  const status = confirmedStatus(reply);
  if (status && (status.state === "idle" || status.state === "recovering") && status.active === null && !status.auto_running) {
    return { kind: "confirmed", status, message: "Controller confirmed the cycle stopped." };
  }
  return unconfirmed("The controller’s answer didn’t confirm the stop. The cycle or a fault may still be running: check status.");
}

export async function postCycleStart(request: CycleRequest, signal?: AbortSignal): Promise<ActionOutcome> {
  const reply = await post((signal) => createControllerClient().POST("/faults/auto/start", { body: request, parseAs: "text", credentials: "omit", signal }), signal);
  return interpretCycleStart(reply, request);
}

export async function postCycleStop(signal?: AbortSignal): Promise<ActionOutcome> {
  const reply = await post((signal) => createControllerClient().POST("/faults/auto/stop", { parseAs: "text", credentials: "omit", signal }), signal);
  return interpretCycleStop(reply);
}
