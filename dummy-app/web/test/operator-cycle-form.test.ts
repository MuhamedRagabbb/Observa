// @vitest-environment node
// P5-C: the automatic-cycle form (src/operator/cycle/cycle-form.ts). Defaults and ranges come from
// the shared contract; what the operator sees is what is sent; nothing the controller would
// refuse or coerce is ever sent; and a running cycle is shown from the controller's own config.
import { describe, expect, it } from "vitest";
import { controllerStatus } from "../e2e/fixtures/controller.ts";
import { defaultCycleForm, formFromAuto, stepField, validateCycle, type CycleForm } from "../src/operator/cycle/cycle-form.ts";

const form = (overrides: Partial<CycleForm> = {}): CycleForm => ({ ...defaultCycleForm(), ...overrides });

describe("defaults", () => {
  it("come from the contract, not from the artboard's sample numbers", () => {
    expect(defaultCycleForm()).toEqual({
      warmup: "120",
      duration: "120",
      recovery: "90",
      repeat: "1",
      scenarios: ["db_connection_exhaustion", "payments_errors", "bad_deployment"],
      shuffle: false,
      seed: "42",
    });
  });
});

describe("the request that is sent", () => {
  it("sends every setting explicitly, scenarios in the contract's order", () => {
    expect(validateCycle(form())).toEqual({
      ok: true,
      request: {
        seed: 42,
        scenarios: ["db_connection_exhaustion", "payments_errors", "bad_deployment"],
        shuffle: false,
        warmup_seconds: 120,
        duration_seconds: 120,
        recovery_seconds: 90,
        repeat: 1,
      },
    });
  });

  it("keeps the operator's choices: a subset, shuffle, seed, decimals", () => {
    const result = validateCycle(form({ scenarios: ["bad_deployment", "db_connection_exhaustion"], shuffle: true, seed: "7341", warmup: "60", duration: "0.5", recovery: "0", repeat: "3" }));
    expect(result).toEqual({
      ok: true,
      request: { seed: 7341, scenarios: ["db_connection_exhaustion", "bad_deployment"], shuffle: true, warmup_seconds: 60, duration_seconds: 0.5, recovery_seconds: 0, repeat: 3 },
    });
  });

  it("the edges of every range are accepted", () => {
    expect(validateCycle(form({ warmup: "0", duration: "0.1", recovery: "0", repeat: "1", seed: "-2147483648" })).ok).toBe(true);
    expect(validateCycle(form({ warmup: "3600", duration: "1800", recovery: "3600", repeat: "1000", seed: "2147483647" })).ok).toBe(true);
  });
});

describe("what is refused before anything is sent", () => {
  it.each([
    ["warmup above 3600", { warmup: "3601" }, "warmup", "Enter 0–3600 s."],
    ["warmup negative", { warmup: "-1" }, "warmup", "Enter 0–3600 s."],
    ["duration below 0.1", { duration: "0.09" }, "duration", "Enter 0.1–1800 s."],
    ["duration above 1800", { duration: "1801" }, "duration", "Enter 0.1–1800 s."],
    ["recovery above 3600", { recovery: "3600.01" }, "recovery", "Enter 0–3600 s."],
    ["repeat zero", { repeat: "0" }, "repeat", "Enter a whole number from 1 to 1000."],
    ["repeat above 1000", { repeat: "1001" }, "repeat", "Enter a whole number from 1 to 1000."],
    ["repeat with a decimal", { repeat: "2.5" }, "repeat", "Enter a whole number from 1 to 1000."],
    ["repeat empty", { repeat: "" }, "repeat", "Enter a whole number from 1 to 1000."],
    ["seed with a decimal", { seed: "1.5" }, "seed", "Enter a whole number from -2147483648 to 2147483647."],
    ["seed above int32", { seed: "2147483648" }, "seed", "Enter a whole number from -2147483648 to 2147483647."],
    ["seed below int32", { seed: "-2147483649" }, "seed", "Enter a whole number from -2147483648 to 2147483647."],
    ["seed in exponent form", { seed: "1e3" }, "seed", "Enter a whole number from -2147483648 to 2147483647."],
    ["seed empty", { seed: " " }, "seed", "Enter a whole number from -2147483648 to 2147483647."],
    ["no scenario", { scenarios: [] }, "scenarios", "Choose at least one scenario."],
    ["a number with a comma", { warmup: "1,5" }, "warmup", "Enter 0–3600 s."],
    ["a number with three decimals", { duration: "1.234" }, "duration", "Enter 0.1–1800 s."],
  ])("%s", (_name, overrides, field, message) => {
    expect(validateCycle(form(overrides as Partial<CycleForm>))).toEqual({ ok: false, errors: { [field]: message } });
  });

  it("every wrong field is reported at once", () => {
    expect(validateCycle(form({ warmup: "x", repeat: "0", scenarios: [] }))).toEqual({
      ok: false,
      errors: { warmup: "Enter 0–3600 s.", repeat: "Enter a whole number from 1 to 1000.", scenarios: "Choose at least one scenario." },
    });
  });
});

describe("the steppers", () => {
  it.each([
    ["warmup", "120", 1, "130"],
    ["warmup", "120", -1, "110"],
    ["warmup", "5", -1, "0"],
    ["warmup", "3595", 1, "3600"],
    ["duration", "120", 1, "130"],
    ["duration", "5", -1, "0.1"],
    ["duration", "1795", 1, "1800"],
    ["recovery", "0", -1, "0"],
    ["recovery", "3600", 1, "3600"],
    ["repeat", "3", 1, "4"],
    ["repeat", "1", -1, "1"],
    ["repeat", "1000", 1, "1000"],
  ] as const)("%s %s step %i gives %s (clamped to the contract's range)", (field, text, direction, expected) => {
    expect(stepField(field, text, direction)).toBe(expected);
  });

  it("starts from the contract default when the text is not a number", () => {
    expect(stepField("warmup", "abc", 1)).toBe("130");
    expect(stepField("repeat", "", 1)).toBe("2");
    expect(stepField("duration", "1.234", -1)).toBe("110");
  });
});

describe("showing the cycle that is running", () => {
  it("rebuilds the form from the controller's own config", () => {
    const auto = controllerStatus("automatic").auto!;
    expect(formFromAuto(auto.config)).toEqual({
      warmup: "60",
      duration: "120",
      recovery: "180",
      repeat: "3",
      scenarios: ["db_connection_exhaustion", "payments_errors"],
      shuffle: true,
      seed: "7341",
    });
  });

  it("applies the controller's defaults to anything it left out", () => {
    expect(formFromAuto({ seed: 1, shuffle: false } as never)).toEqual({
      warmup: "120",
      duration: "120",
      recovery: "90",
      repeat: "1",
      scenarios: ["db_connection_exhaustion", "payments_errors", "bad_deployment"],
      shuffle: false,
      seed: "1",
    });
  });
});
