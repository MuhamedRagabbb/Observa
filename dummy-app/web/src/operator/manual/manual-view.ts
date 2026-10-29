import type { Connection, ControllerStatus } from "../status/experiment.ts";
import { formFromActive, type FormValues } from "./fault-form.ts";

const LOCK_NOTICES: Record<string, string> = {
  fault_active: "One manual fault at a time. Stop the active fault to change parameters.",
  auto_running: "Manual injection is paused while the automatic cycle runs.",
  recovering: "Unavailable while services recover from the last fault.",
  status_unknown: "Unavailable until the fault status is confirmed.",
};

export function manualView({ connection, status, draft, stale = false }: { connection: Connection; status: ControllerStatus | null; draft: FormValues; stale?: boolean }) {
  const locked = connection !== "connected" || stale || status?.controls.activate?.enabled !== true;
  const activeForm = locked && status?.state === "manual" && status.active ? formFromActive(status.active) : null;
  const form = activeForm ?? draft;
  const active = status?.active;
  const activation: "pending" | "acknowledged" | null = !active ? null
    : active.status === "active" && active.acknowledged_at ? "acknowledged" : "pending";
  let lockNotice: string | null = null;
  if (connection === "disconnected") lockNotice = "Unavailable while the controller is unreachable.";
  else if (connection !== "connected" || !status) lockNotice = "Unavailable until the controller answers.";
  else if (stale) lockNotice = "Waiting for a fresh answer from the controller.";
  else if (locked) {
    const reason = status.controls.activate?.reason;
    lockNotice = reason && Object.hasOwn(LOCK_NOTICES, reason) ? LOCK_NOTICES[reason] ?? "Unavailable right now." : "Unavailable right now.";
  }
  return { locked, lockNotice, selected: form.scenario, activeScenario: activeForm?.scenario ?? null, activation, form };
}
