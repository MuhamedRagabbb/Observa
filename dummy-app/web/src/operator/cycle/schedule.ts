import type { components } from "../../api/generated/controller.ts";
import { FAULT_CONTRACT } from "../fault-contract.ts";

export type Segment = { kind: "warmup" | "fault" | "recovery"; seconds: number };

export function plannedSchedule({ warmup, duration, recovery, repeat, scenarioCount }: { warmup: number; duration: number; recovery: number; repeat: number; scenarioCount: number }) {
  const steps = repeat * scenarioCount;
  const totalSeconds = warmup + steps * (duration + recovery);
  if (steps > 60) return { steps, totalSeconds, segments: null };
  const segments: Segment[] = [];
  if (warmup > 0) segments.push({ kind: "warmup", seconds: warmup });
  for (let step = 0; step < steps; step++) {
    segments.push({ kind: "fault", seconds: duration });
    if (recovery > 0) segments.push({ kind: "recovery", seconds: recovery });
  }
  return { steps, totalSeconds, segments };
}

export function scheduleFromAutoConfig(config: Partial<components["schemas"]["AutoConfig"]>) {
  const defaults = FAULT_CONTRACT.auto;
  return plannedSchedule({
    warmup: config.warmup_seconds ?? defaults.warmup_seconds.default,
    duration: config.duration_seconds ?? defaults.duration_seconds.default,
    recovery: config.recovery_seconds ?? defaults.recovery_seconds.default,
    repeat: config.repeat ?? defaults.repeat.default,
    scenarioCount: (config.scenarios ?? defaults.scenarios.default).length,
  });
}

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds * 100) / 100;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = Math.round((total % 60) * 100) / 100;
  const parts: string[] = [];
  if (hours > 0) parts.push(`${hours} h`);
  if (minutes > 0) parts.push(`${minutes} min`);
  if (rest > 0 || parts.length === 0) parts.push(`${rest} s`);
  return parts.join(" ");
}

export function scheduleProgress(segments: readonly Segment[] | null, auto: Pick<components["schemas"]["AutoStatus"], "phase" | "step"> | null): { now: number | null; done: number } {
  const unknown = { now: null, done: 0 };
  if (!segments || !auto || auto.phase === "stopping") return unknown;
  if (auto.phase === "warmup") {
    const index = segments.findIndex((segment) => segment.kind === "warmup");
    return index < 0 ? unknown : { now: index, done: index };
  }
  if (!Number.isInteger(auto.step) || auto.step < 1) return unknown;
  let step = 0;
  for (let index = 0; index < segments.length; index++) {
    if (segments[index]?.kind !== "fault") continue;
    step++;
    if (step !== auto.step) continue;
    if (auto.phase === "fault") return { now: index, done: index };
    if (auto.phase === "recovery") return { now: segments[index + 1]?.kind === "recovery" ? index + 1 : null, done: index + 1 };
  }
  return unknown;
}
