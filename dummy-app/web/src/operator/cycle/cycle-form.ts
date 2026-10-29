import type { components } from "../../api/generated/controller.ts";
import { FAULT_CONTRACT, type ScenarioId } from "../fault-contract.ts";

export type CycleForm = {
  warmup: string;
  duration: string;
  recovery: string;
  repeat: string;
  scenarios: ScenarioId[];
  shuffle: boolean;
  seed: string;
};
export type CycleRequest = Required<components["schemas"]["AutoConfig"]>;

const AUTO = FAULT_CONTRACT.auto;
const RANGES = { warmup: AUTO.warmup_seconds, duration: AUTO.duration_seconds, recovery: AUTO.recovery_seconds, repeat: AUTO.repeat };
type StepField = keyof typeof RANGES;
const textNumber = (text: unknown, pattern: RegExp): number => typeof text === "string" && text === text.trim() && pattern.test(text) ? Number(text) : NaN;
const seconds = /^\d+(\.\d{1,2})?$/;
const whole = /^\d+$/;
const trim = (value: number) => String(Math.round(value * 100) / 100);
const inRange = (value: number, range: { min: number; max: number }) => Number.isFinite(value) && value >= range.min && value <= range.max;

export function defaultCycleForm(): CycleForm {
  return formFromAuto({});
}

export function formFromAuto(config: Partial<components["schemas"]["AutoConfig"]>): CycleForm {
  return {
    warmup: trim(config.warmup_seconds ?? AUTO.warmup_seconds.default),
    duration: trim(config.duration_seconds ?? AUTO.duration_seconds.default),
    recovery: trim(config.recovery_seconds ?? AUTO.recovery_seconds.default),
    repeat: String(config.repeat ?? AUTO.repeat.default),
    scenarios: [...(config.scenarios ?? AUTO.scenarios.default)],
    shuffle: config.shuffle ?? AUTO.shuffle.default,
    seed: String(config.seed ?? AUTO.seed.default),
  };
}

export function validateCycle(form: CycleForm): { ok: true; request: CycleRequest } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const values = {
    warmup: textNumber(form.warmup, seconds),
    duration: textNumber(form.duration, seconds),
    recovery: textNumber(form.recovery, seconds),
    repeat: textNumber(form.repeat, whole),
  };
  for (const field of ["warmup", "duration", "recovery", "repeat"] as const) {
    const range = RANGES[field];
    if (!inRange(values[field], range)) {
      errors[field] = field === "repeat" ? `Enter a whole number from ${range.min} to ${range.max}.` : `Enter ${range.min}–${range.max} s.`;
    }
  }
  const scenarios = FAULT_CONTRACT.scenarios.filter((scenario) => form.scenarios.includes(scenario.id)).map((scenario) => scenario.id);
  if (scenarios.length < AUTO.scenarios.min_items) errors.scenarios = "Choose at least one scenario.";
  const seed = textNumber(form.seed, /^-?\d+$/);
  if (!inRange(seed, { min: -2147483648, max: 2147483647 })) errors.seed = "Enter a whole number from -2147483648 to 2147483647.";
  return Object.keys(errors).length > 0
    ? { ok: false, errors }
    : { ok: true, request: { seed, scenarios, shuffle: form.shuffle, warmup_seconds: values.warmup, duration_seconds: values.duration, recovery_seconds: values.recovery, repeat: values.repeat } };
}

export function stepField(field: StepField, text: string, direction: -1 | 1): string {
  const range = RANGES[field];
  const value = textNumber(text, field === "repeat" ? whole : seconds);
  const base = Number.isFinite(value) ? value : range.default;
  return trim(Math.min(range.max, Math.max(range.min, base + direction * (field === "repeat" ? 1 : 10))));
}
