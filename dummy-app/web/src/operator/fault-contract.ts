// The fault and cycle settings the controller accepts (contracts/fault-parameters.json). The
// ranges are not in OpenAPI, so the JSON is the shared source: pytest checks it against the
// controller's validators and test/fault-contract.test.ts against the OpenAPI snapshot.
import contract from "../../contracts/fault-parameters.json" with { type: "json" };
import type { components } from "../api/generated/controller.ts";

export type ScenarioId = components["schemas"]["Activation"]["scenario"];

type Range = { min: number; max: number; default: number; unit?: string; integer?: boolean };
export type RangeParameter = Range & { name: string; kind: "range" };
export type ChoiceParameter = { name: string; kind: "choice"; choices: number[]; default: number };
export type FaultParameter = RangeParameter | ChoiceParameter;

export type ScenarioContract = {
  id: ScenarioId;
  code: "F1" | "F2" | "F3";
  parameters: FaultParameter[];
  /** Settings the scenario applies that the operator cannot change (shown, never as a control). */
  fixed?: Record<string, string>;
};

export type FaultContract = {
  version: 1;
  scenarios: ScenarioContract[];
  duration_seconds: Range;
  auto: {
    warmup_seconds: Range;
    duration_seconds: Range;
    recovery_seconds: Range;
    repeat: Range;
    scenarios: { min_items: number; max_items: number; default: ScenarioId[] };
    shuffle: { default: boolean };
    seed: { default: number; integer: true };
  };
};

export const FAULT_CONTRACT = contract as FaultContract;

export function scenarioContract(id: ScenarioId): ScenarioContract {
  const found = FAULT_CONTRACT.scenarios.find((scenario) => scenario.id === id);
  if (!found) throw new Error(`Unknown scenario: ${String(id)}`);
  return found;
}
