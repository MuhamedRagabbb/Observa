// The controller's state for the Operator Dashboard: GET /faults/status every 2.5 s while the
// page is visible (React Query pauses the interval while it is hidden and reads at once when it is
// shown again), each read given up after 5 s. A failed, timed-out or unreadable answer means the
// controller is unreachable: the last confirmed answer is kept, labelled as such, never refreshed
// by guesswork. Read-only: this module never sends anything but GET.
import { useQuery } from "@tanstack/react-query";
import { createControllerClient } from "../../api/controller-client.ts";
import type { components } from "../../api/generated/controller.ts";
import type { Connection } from "./experiment.ts";

export type ControllerStatus = components["schemas"]["ControllerStatus"];
export const STATUS_POLL_MS = 2500;
export const STATUS_TIMEOUT_MS = 5000;

const STATES = new Set(["idle", "manual", "automatic", "recovering", "unknown"]);
const PHASES = new Set(["warmup", "fault", "recovery", "stopping"]);
const CONTROLS = ["activate", "auto_start", "deactivate", "auto_stop"];

/** The controller could not be read: network, http_<status>, timeout or invalid_response. */
export class ControllerUnreachable extends Error {
  constructor(readonly reason: string) {
    super(`Controller unreachable: ${reason}`);
    this.name = "ControllerUnreachable";
  }
}

export const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
export const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function validControls(controls: unknown): boolean {
  return isRecord(controls) && CONTROLS.every((key) => {
    if (!Object.hasOwn(controls, key)) return true;
    const control = controls[key];
    return isRecord(control) && typeof control.enabled === "boolean"
      && (control.reason === null || typeof control.reason === "string");
  });
}

/** A ControllerStatus this page can trust to describe the controller, or an error. */
export function parseControllerStatus(body: unknown): ControllerStatus {
  const ok =
    isRecord(body) &&
    typeof body.state === "string" &&
    STATES.has(body.state) &&
    isNumber(body.now) &&
    typeof body.auto_running === "boolean" &&
    validControls(body.controls) &&
    (body.active === null || (isRecord(body.active) && typeof body.active.scenario === "string" && isNumber(body.active.expires_at) && typeof body.active.started_at === "string" && isRecord(body.active.parameters))) &&
    (body.auto === null ||
      (isRecord(body.auto) &&
        typeof body.auto.phase === "string" &&
        PHASES.has(body.auto.phase) &&
        isNumber(body.auto.repeat) &&
        isNumber(body.auto.repeats) &&
        isNumber(body.auto.phase_started_at) &&
        (body.auto.phase_ends_at === null || isNumber(body.auto.phase_ends_at)))) &&
    (body.last_fault === null || isRecord(body.last_fault)) &&
    // A manual fault always has its incident; a cycle may answer without details while stopping.
    (body.state !== "manual" || body.active !== null);
  if (!ok) throw new ControllerUnreachable("invalid_response");
  return body as ControllerStatus;
}

export type StatusReading = { status: ControllerStatus; /** performance.now() when it arrived */ receivedAt: number };

/** A read shares the controller's timeout and cancellation rules; parsing stays with its caller. */
export async function fetchControllerRead<T>(
  read: (signal: AbortSignal) => Promise<{ data?: unknown; response: Response }>,
  parse: (body: unknown) => T,
  signal?: AbortSignal,
  timeoutMs = STATUS_TIMEOUT_MS,
): Promise<T> {
  const limit = new AbortController();
  const onAbort = () => limit.abort();
  if (signal?.aborted) limit.abort();
  signal?.addEventListener("abort", onAbort);
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    limit.abort();
  }, timeoutMs);
  try {
    let result;
    try {
      result = await read(limit.signal);
    } catch {
      throw new ControllerUnreachable(timedOut ? "timeout" : "network");
    }
    if (!result.response.ok) throw new ControllerUnreachable(`http_${result.response.status}`);
    return parse(result.data);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

/** New reads request text so malformed JSON is an invalid answer, not a network failure. */
export function parseControllerJson(body: unknown): unknown {
  if (typeof body !== "string") throw new ControllerUnreachable("invalid_response");
  try {
    return JSON.parse(body) as unknown;
  } catch {
    throw new ControllerUnreachable("invalid_response");
  }
}

export function fetchControllerStatus(signal?: AbortSignal): Promise<StatusReading> {
  return fetchControllerRead(
    (signal) => createControllerClient().GET("/faults/status", { signal }),
    (body) => ({ status: parseControllerStatus(body), receivedAt: performance.now() }),
    signal,
  );
}

export function useControllerStatus() {
  const query = useQuery({
    queryKey: ["controller-status"],
    queryFn: ({ signal }) => fetchControllerStatus(signal),
    refetchInterval: STATUS_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    retry: false,
  });
  const connection: Connection = query.isError ? "disconnected" : query.data ? "connected" : "connecting";
  return {
    /** The last confirmed answer (kept while the controller is unreachable). */
    reading: query.data ?? null,
    connection,
    refreshing: query.isFetching,
    /** Read the status again now (Retry connection, Check status): a GET, nothing else. */
    refresh: async () => {
      await query.refetch({ cancelRefetch: true });
    },
  };
}
