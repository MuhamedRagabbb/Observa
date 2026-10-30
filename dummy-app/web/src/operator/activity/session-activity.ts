// The controller has no activity ids. Canonical content gives repeated polls the same keys,
// including entries with the same timestamp and event but different details.
import type { components } from "../../api/generated/controller.ts";
import type { ScenarioId } from "../fault-contract.ts";
import { faultSettingsSummary, SCENARIO_LABELS, scenarioCode } from "../scenarios.ts";
import { formatTime } from "../status/experiment.ts";

export type ActivityEntry = components["schemas"]["ActivityEntry"];
export type ActivityItem = { key: string; time: string; text: string };

const words = (value: unknown) => typeof value === "string" ? value.replaceAll("_", " ") : "";
const sentence = (value: unknown) => {
  const text = words(value);
  return text.charAt(0).toUpperCase() + text.slice(1);
};
const number = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const known = (value: unknown): value is ScenarioId => typeof value === "string" && Object.hasOwn(SCENARIO_LABELS, value);
const scenarioName = (value: unknown) => known(value) ? `${scenarioCode(value)} ${SCENARIO_LABELS[value]}` : words(value);
const join = (...parts: string[]) => parts.filter(Boolean).join(" · ");

const REASONS: Record<string, string> = {
  manual: "stopped manually",
  target_lease_expired: "lease expired",
  auto_stopped: "cycle stopped",
  ttl: "time ran out",
  schedule: "cycle step ended",
  controller_restart: "controller restarted",
};

export function describeEntry(entry: ActivityEntry): string {
  const scenario = scenarioName(entry.scenario);
  switch (entry.event) {
    case "fault_requested":
      return join("Fault requested", scenario,
        known(entry.scenario) ? faultSettingsSummary(entry.scenario, record(entry.parameters) ? entry.parameters : {}) : "",
        number(entry.duration_seconds) ? `${entry.duration_seconds} s` : "");
    case "fault_active":
      return "Controller confirmed fault is active";
    case "activation_unconfirmed":
      return join("Fault start not confirmed", scenario);
    case "stop_requested":
      return join("Stop requested", scenario);
    case "fault_stopped":
      return join("Fault stopped", scenario, typeof entry.reason === "string" && Object.hasOwn(REASONS, entry.reason) ? REASONS[entry.reason] ?? "" : words(entry.reason));
    case "stop_unconfirmed":
      return join("Stop not confirmed by the affected service", scenario);
    case "cycle_started": {
      const scenarios = Array.isArray(entry.scenarios) ? entry.scenarios.map((value: unknown) => known(value) ? scenarioCode(value) : words(value)).filter(Boolean).join(", ") : "";
      return join("Cycle started", number(entry.repeats) ? `${entry.repeats} ${entry.repeats === 1 ? "repeat" : "repeats"}` : "", scenarios,
        typeof entry.shuffle === "boolean" ? entry.shuffle ? "shuffled" : "in order" : "",
        entry.shuffle === true && number(entry.seed) ? `seed ${entry.seed}` : "");
    }
    case "phase_started":
      return join(entry.phase ? `${sentence(entry.phase)} phase started` : "Phase started",
        entry.phase !== "warmup" && number(entry.repeat) ? `repeat ${entry.repeat}` : "",
        entry.phase !== "warmup" ? scenario : "");
    case "service_health_changed":
      return typeof entry.service === "string" && typeof entry.state === "string"
        ? join(entry.service, typeof entry.previous === "string" ? `${sentence(entry.previous)} → ${sentence(entry.state)}` : `now ${sentence(entry.state)}`)
        : "Service health changed";
    case "services_recovered":
      return scenario ? `Services recovered after ${scenario}` : "Services recovered";
    case "controller_started":
      return join("Controller started", number(entry.interrupted_incidents) && entry.interrupted_incidents > 0
        ? `${entry.interrupted_incidents} interrupted ${entry.interrupted_incidents === 1 ? "fault" : "faults"} cleaned up` : "");
    case "control_rejected":
      return join("Controller refused the request", words(entry.action), words(entry.error_code));
    default:
      return sentence(entry.event);
  }
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (record(value)) return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonical(value[key])]));
  return value;
}

export function sessionActivity({ entries, sessionStart, timeZone }: { entries: ActivityEntry[]; sessionStart: number; timeZone?: string }): ActivityItem[] {
  const seen = new Set<string>();
  return entries
    .map((entry) => ({ entry, at: typeof entry.at === "string" ? Date.parse(entry.at) : NaN }))
    .filter(({ at }) => Number.isFinite(at) && at / 1000 >= sessionStart)
    .sort((a, b) => b.at - a.at)
    .flatMap(({ entry }) => {
      const key = JSON.stringify(canonical(entry));
      if (seen.has(key)) return [];
      seen.add(key);
      return [{ key, time: formatTime(entry.at, timeZone) ?? "—", text: describeEntry(entry) }];
    });
}
