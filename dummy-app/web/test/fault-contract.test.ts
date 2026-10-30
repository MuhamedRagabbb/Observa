// @vitest-environment node
// P5-0: contracts/fault-parameters.json holds the fault and cycle settings the Operator Dashboard
// offers. pytest (tests/unit/test_fault_parameter_contract.py) checks it against the controller's
// validators; this test checks the typed view the operator code uses, and that it agrees with
// everything the controller's OpenAPI snapshot can express.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FAULT_CONTRACT, scenarioContract } from "../src/operator/fault-contract.ts";

const read = (...parts: string[]) => JSON.parse(readFileSync(join(import.meta.dirname, "..", ...parts), "utf8"));
const contract = read("contracts", "fault-parameters.json");
const schemas = read("openapi", "controller.json").components.schemas;
type Bound = { default?: unknown; minimum?: number; maximum?: number; type?: string; minItems?: number; maxItems?: number };

describe("fault parameter contract (web view)", () => {
  it("is the shared contract file, unchanged", () => {
    expect(FAULT_CONTRACT).toEqual(contract);
  });

  it("lists exactly the controller's scenarios, in its order, as F1, F2 and F3", () => {
    expect(FAULT_CONTRACT.scenarios.map((s) => s.id)).toEqual(schemas.Activation.properties.scenario.enum);
    expect(FAULT_CONTRACT.auto.scenarios.default).toEqual(schemas.AutoConfig.properties.scenarios.items.enum);
    expect(FAULT_CONTRACT.scenarios.map((s) => s.code)).toEqual(["F1", "F2", "F3"]);
  });

  it("matches the OpenAPI bounds and defaults of the manual duration and every cycle setting", () => {
    const duration: Bound = schemas.Activation.properties.duration_seconds;
    expect([FAULT_CONTRACT.duration_seconds.min, FAULT_CONTRACT.duration_seconds.max, FAULT_CONTRACT.duration_seconds.default]).toEqual([
      duration.minimum,
      duration.maximum,
      duration.default,
    ]);
    const auto = schemas.AutoConfig.properties as Record<string, Bound>;
    expect(Object.keys(FAULT_CONTRACT.auto).sort()).toEqual(Object.keys(auto).sort());
    for (const field of ["warmup_seconds", "duration_seconds", "recovery_seconds", "repeat"] as const) {
      const spec = FAULT_CONTRACT.auto[field];
      expect([spec.min, spec.max, spec.default], field).toEqual([auto[field]?.minimum, auto[field]?.maximum, auto[field]?.default]);
      expect(Boolean("integer" in spec && spec.integer), field).toBe(auto[field]?.type === "integer");
    }
    expect([FAULT_CONTRACT.auto.scenarios.min_items, FAULT_CONTRACT.auto.scenarios.max_items]).toEqual([auto.scenarios?.minItems, auto.scenarios?.maxItems]);
    expect(FAULT_CONTRACT.auto.shuffle.default).toBe(auto.shuffle?.default);
    expect(FAULT_CONTRACT.auto.seed.default).toBe(auto.seed?.default);
  });

  it("finds a scenario's settings by its id, and refuses an unknown one", () => {
    expect(scenarioContract("payments_errors").parameters.map((p) => p.name)).toEqual(["failure_rate", "status_code"]);
    expect(scenarioContract("bad_deployment").fixed).toEqual({ release_version: "v2-buggy" });
    expect(() => scenarioContract("unknown" as never)).toThrow(/unknown scenario/i);
  });
});
