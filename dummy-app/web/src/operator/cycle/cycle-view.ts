import type { Connection, ControllerStatus } from "../status/experiment.ts";
import { formFromAuto, type CycleForm } from "./cycle-form.ts";

const LOCK_NOTICES: Record<string, string> = {
  fault_active: "Unavailable while a manual fault is active.",
  auto_running: "Cycle running. Settings are locked until it stops.",
  recovering: "Unavailable while services recover from the last fault.",
  status_unknown: "Unavailable until the fault status is confirmed.",
};

export function cycleView({ connection, status, draft, stale = false }: { connection: Connection; status: ControllerStatus | null; draft: CycleForm; stale?: boolean }) {
  const locked = connection !== "connected" || stale || status?.controls.auto_start?.enabled !== true;
  const running = status?.state === "automatic";
  const auto = running ? status.auto : null;
  const form = auto ? formFromAuto(auto.config) : draft;
  const progress = !running ? null : auto ? `Repeat ${auto.repeat} of ${auto.repeats} · ${auto.phase}${auto.phase === "stopping" ? "" : " phase"}` : "Stopping the cycle";
  let lockNotice: string | null = null;
  if (connection === "disconnected") lockNotice = "Unavailable while the controller is unreachable.";
  else if (connection !== "connected" || !status) lockNotice = "Unavailable until the controller answers.";
  else if (stale) lockNotice = "Waiting for a fresh answer from the controller.";
  else if (locked) {
    const reason = status.controls.auto_start?.reason;
    lockNotice = reason && Object.hasOwn(LOCK_NOTICES, reason) ? LOCK_NOTICES[reason] ?? "Unavailable right now." : "Unavailable right now.";
  }
  return { locked, lockNotice, running, progress, form };
}
