// @vitest-environment node
// P5-0: the controller answers the Operator Dashboard's mocked browser tests are built from
// (e2e/fixtures/controller.ts). They must be answers the real controller could give: every field
// its OpenAPI schema requires, the controls it enables in that state (controller-facts.json,
// checked against the controller by tests/unit/test_operator_fixtures.py), its real service names,
// parameters inside the contract, and never any ground truth.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import facts from "../e2e/fixtures/controller-facts.json";
import { activity, activityEntry, CONTROLLER_NOW, controllerStatus, healthSnapshot, STATES } from "../e2e/fixtures/controller.ts";
import { scenarioContract } from "../src/operator/fault-contract.ts";

type Schema = { required?: string[]; properties?: Record<string, { $ref?: string; anyOf?: { $ref?: string }[]; items?: { $ref?: string } }> };
const schemas: Record<string, Schema> = JSON.parse(readFileSync(join(import.meta.dirname, "..", "openapi", "controller.json"), "utf8")).components.schemas;

/** Every field the schema requires is present, recursively through referenced schemas. */
function missing(value: unknown, schemaName: string, path = schemaName): string[] {
  const schema = schemas[schemaName];
  if (!schema || value === null || typeof value !== "object") return [];
  const record = value as Record<string, unknown>;
  const gaps = (schema.required ?? []).filter((key) => !(key in record)).map((key) => `${path}.${key}`);
  for (const [key, property] of Object.entries(schema.properties ?? {})) {
    const ref = property.$ref ?? property.anyOf?.find((option) => option.$ref)?.$ref ?? property.items?.$ref;
    if (!ref || record[key] == null) continue;
    const name = ref.split("/").pop() as string;
    const values = Array.isArray(record[key]) ? (record[key] as unknown[]) : [record[key]];
    values.forEach((item, index) => gaps.push(...missing(item, name, `${path}.${key}${Array.isArray(record[key]) ? `[${index}]` : ""}`)));
  }
  return gaps;
}

const GROUND_TRUTH = /root_cause|expected_signals|signals/;

describe("controller status fixtures", () => {
  it.each(STATES)("%s: a complete ControllerStatus with the controller's own controls for that state", (state) => {
    const status = controllerStatus(state);
    expect(missing(status, "ControllerStatus")).toEqual([]);
    expect(status.state).toBe(state);
    expect(status.controls).toEqual(facts.controls[state]);
    expect(status.auto_running).toBe(state === "automatic");
    expect(status.now).toBe(CONTROLLER_NOW);
    expect(JSON.stringify(status)).not.toMatch(GROUND_TRUTH);
  });

  it("a manual fault has an incident that ends in the future, with parameters inside the contract", () => {
    const { active } = controllerStatus("manual");
    expect(active?.status).toBe("active");
    expect(active?.expires_at).toBeGreaterThan(CONTROLLER_NOW);
    const contract = scenarioContract(active?.scenario ?? "payments_errors");
    for (const [name, value] of Object.entries(active?.parameters ?? {})) {
      const parameter = contract.parameters.find((p) => p.name === name);
      expect(parameter, name).toBeDefined();
      if (parameter?.kind === "range") expect(value as number).toBeGreaterThanOrEqual(parameter.min);
      if (parameter?.kind === "range") expect(value as number).toBeLessThanOrEqual(parameter.max);
      if (parameter?.kind === "choice") expect(parameter.choices).toContain(value);
    }
  });

  it("only the states that have them carry an incident, a cycle or a last fault", () => {
    expect(STATES.filter((s) => controllerStatus(s).active !== null)).toEqual(["manual", "automatic", "unknown"]);
    expect(STATES.filter((s) => controllerStatus(s).auto !== null)).toEqual(["automatic"]);
    expect(controllerStatus("unknown").active?.status).toBe("deactivation_unconfirmed");
    expect(controllerStatus("recovering").last_fault).toMatchObject({ end_reason: "manual" });
    const auto = controllerStatus("automatic").auto;
    expect(auto?.phase_ends_at).toBeGreaterThan(CONTROLLER_NOW);
    expect(auto?.repeat).toBeLessThanOrEqual(auto?.repeats ?? 0);
  });

  it("takes overrides without losing required fields", () => {
    const status = controllerStatus("manual", { active: { ...controllerStatus("manual").active!, scenario: "bad_deployment", parameters: { delay_seconds: 1.5 } } });
    expect(status.active?.scenario).toBe("bad_deployment");
    expect(missing(status, "ControllerStatus")).toEqual([]);
  });
});

describe("health and activity fixtures", () => {
  it("health lists the controller's services, healthy by default, overridable one by one", () => {
    const snapshot = healthSnapshot({ "payments-service": { state: "degraded", reasons: ["high_error_ratio"] } });
    expect(missing(snapshot, "HealthSnapshot")).toEqual([]);
    expect(snapshot.services.map((row) => row.service)).toEqual(facts.health_services);
    expect(snapshot.services.map((row) => row.state)).toEqual(["healthy", "healthy", "healthy", "degraded"]);
    expect(snapshot.metrics).toBe("available");
  });

  it("activity is newest first, inside the controller's capacity, with ISO times", () => {
    const log = activity([
      activityEntry("fault_requested", 0, { scenario: "payments_errors", duration_seconds: 300, parameters: { failure_rate: 0.9, status_code: 503 } }),
      activityEntry("fault_active", 1, { scenario: "payments_errors" }),
    ]);
    expect(missing(log, "Activity")).toEqual([]);
    expect(log.capacity).toBe(200);
    expect(log.entries.map((entry) => entry.event)).toEqual(["fault_active", "fault_requested"]);
    for (const entry of log.entries) expect(entry.at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/);
    expect(JSON.stringify(log)).not.toMatch(GROUND_TRUTH);
  });
});
