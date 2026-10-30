// How the Operator Dashboard names each fault scenario and says what it is doing. The F-codes and
// the settings' defaults come from the shared contract (contracts/fault-parameters.json); a
// setting the controller was not sent is the default the controller applies.
import { scenarioContract, type ScenarioId } from "./fault-contract.ts";

export const SCENARIO_LABELS: Record<ScenarioId, string> = {
  db_connection_exhaustion: "Database Exhaustion",
  payments_errors: "Payment Errors",
  bad_deployment: "Bad Deployment",
};

export const scenarioCode = (id: ScenarioId) => scenarioContract(id).code;

const number = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);

function setting(id: ScenarioId, parameters: Record<string, unknown>, name: string): number {
  const given = number(parameters[name]);
  if (given !== null) return given;
  const parameter = scenarioContract(id).parameters.find((p) => p.name === name);
  if (!parameter) throw new Error(`Unknown setting ${name} for ${id}`);
  return parameter.default;
}

const trim = (value: number) => String(Math.round(value * 100) / 100);

/** Compact settings for activity entries, with the same defaults as the running fault. */
export function faultSettingsSummary(id: ScenarioId, parameters: Record<string, unknown>): string {
  switch (id) {
    case "db_connection_exhaustion":
      return `${trim(setting(id, parameters, "hold_seconds"))} s hold`;
    case "payments_errors":
      return `${Math.round(setting(id, parameters, "failure_rate") * 100)}% · HTTP ${setting(id, parameters, "status_code")}`;
    case "bad_deployment":
      return `${trim(setting(id, parameters, "delay_seconds"))} s delay`;
  }
}

/** One sentence on what the running fault does, with its settings. */
export function faultSummary(id: ScenarioId, parameters: Record<string, unknown>): string {
  switch (id) {
    case "db_connection_exhaustion":
      return `Orders database connections are held for ${trim(setting(id, parameters, "hold_seconds"))} s each.`;
    case "payments_errors":
      return `${Math.round(setting(id, parameters, "failure_rate") * 100)}% of payment requests fail with HTTP ${setting(id, parameters, "status_code")}.`;
    case "bad_deployment":
      return `Orders run on release ${scenarioContract(id).fixed?.release_version ?? "v2-buggy"} with ${trim(setting(id, parameters, "delay_seconds"))} s added delay.`;
  }
}
