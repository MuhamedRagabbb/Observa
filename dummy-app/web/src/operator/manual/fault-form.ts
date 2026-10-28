import type { components } from "../../api/generated/controller.ts";
import { FAULT_CONTRACT, scenarioContract, type ScenarioId } from "../fault-contract.ts";

export type FormValues = { scenario: ScenarioId; settings: Record<string, string>; duration: string };
export type ActivationRequest = components["schemas"]["Activation"] & { parameters: Record<string, number> };

const textNumber = (text: unknown): number => typeof text === "string" && text === text.trim() && /^\d+(\.\d{1,2})?$/.test(text) ? Number(text) : NaN;
const trim = (value: number) => String(Math.round(value * 100) / 100);
const inRange = (value: number, range: { min: number; max: number }) => Number.isFinite(value) && value >= range.min && value <= range.max;

export function defaultForm(scenario: ScenarioId): FormValues {
  return {
    scenario,
    settings: Object.fromEntries(scenarioContract(scenario).parameters.map((parameter) => [
      parameter.name, trim(parameter.default * (parameter.name === "failure_rate" ? 100 : 1)),
    ])),
    duration: String(FAULT_CONTRACT.duration_seconds.default),
  };
}

export function validateForm(form: FormValues): { ok: true; request: ActivationRequest } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const parameters: Record<string, number> = {};
  for (const parameter of scenarioContract(form.scenario).parameters) {
    const value = textNumber(form.settings[parameter.name]);
    if (parameter.kind === "choice") {
      if (!parameter.choices.includes(value)) errors[parameter.name] = `Choose ${parameter.choices.join(" or ")}.`;
      else parameters[parameter.name] = value;
    } else {
      const percent = parameter.name === "failure_rate";
      const scale = percent ? 100 : 1;
      const range = { min: parameter.min * scale, max: parameter.max * scale };
      if (!inRange(value, range) || (parameter.integer && !Number.isInteger(value))) {
        errors[parameter.name] = `Enter ${range.min}–${range.max} ${percent ? "%" : parameter.unit ?? "s"}.`;
      } else {
        parameters[parameter.name] = percent ? Math.round(value * 100) / 10000 : value;
      }
    }
  }
  const duration = textNumber(form.duration);
  const range = FAULT_CONTRACT.duration_seconds;
  if (!inRange(duration, range)) errors.duration = `Enter ${range.min}–${range.max} s.`;
  return Object.keys(errors).length > 0
    ? { ok: false, errors }
    : { ok: true, request: { scenario: form.scenario, duration_seconds: duration, parameters } };
}

export function durationNote(text: string): string | null {
  const seconds = textNumber(text);
  if (!inRange(seconds, FAULT_CONTRACT.duration_seconds)) return null;
  return seconds >= 60 ? `= ${trim(seconds / 60)} min` : `= ${trim(seconds)} s`;
}

export function formFromActive(active: components["schemas"]["OperatorIncident"]): FormValues | null {
  if (!FAULT_CONTRACT.scenarios.some((scenario) => scenario.id === active.scenario)) return null;
  const form = defaultForm(active.scenario);
  for (const parameter of scenarioContract(active.scenario).parameters) {
    const value = active.parameters[parameter.name];
    if (typeof value === "number" && Number.isFinite(value)) form.settings[parameter.name] = trim(value * (parameter.name === "failure_rate" ? 100 : 1));
  }
  // Both timestamps belong to the controller, so the browser's clock cannot change the span.
  const duration = active.expires_at - Date.parse(active.started_at) / 1000;
  if (Number.isFinite(duration) && duration > 0) {
    const whole = Math.round(duration);
    form.duration = Math.abs(duration - whole) <= 0.01 ? String(whole) : trim(duration);
  }
  return form;
}
