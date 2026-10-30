import { validateCycle, type CycleForm } from "../cycle/cycle-form.ts";
import { formatDuration, plannedSchedule } from "../cycle/schedule.ts";
import { validateForm, type FormValues } from "../manual/fault-form.ts";
import { faultSettingsSummary, scenarioCode } from "../scenarios.ts";

export function manualSummary(form: FormValues): string {
  const result = validateForm(form);
  const code = scenarioCode(form.scenario);
  if (!result.ok) return `${code} · settings need attention`;
  return `${code} · ${faultSettingsSummary(form.scenario, result.request.parameters)} · ${Math.round(result.request.duration_seconds * 100) / 100} s`;
}

export function cycleSummary(form: CycleForm): string {
  const result = validateCycle(form);
  if (!result.ok) return Object.keys(result.errors).length === 1 && result.errors.scenarios
    ? "No scenario selected" : "Settings need attention";
  const request = result.request;
  const schedule = plannedSchedule({
    warmup: request.warmup_seconds, duration: request.duration_seconds, recovery: request.recovery_seconds,
    repeat: request.repeat, scenarioCount: request.scenarios.length,
  });
  return `${request.repeat} ${request.repeat === 1 ? "repeat" : "repeats"} · ${formatDuration(schedule.totalSeconds)}${request.shuffle ? " · shuffled" : ""}`;
}

export function lockedSummary(summary: string, locked: boolean): string {
  return locked ? `Locked · ${summary}` : summary;
}
