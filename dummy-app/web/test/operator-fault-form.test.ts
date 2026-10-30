// @vitest-environment node
// P5-B: the manual fault form (src/operator/manual/fault-form.ts). Settings come from the shared
// contract; what the operator sees is exactly what is sent (percent text becomes the ratio the
// controller expects, every setting is sent explicitly), and nothing outside the controller's
// ranges is ever sent.
import { describe, expect, it } from "vitest";
import { CONTROLLER_NOW, controllerStatus } from "../e2e/fixtures/controller.ts";
import { defaultForm, durationNote, formFromActive, validateForm, type FormValues } from "../src/operator/manual/fault-form.ts";

const form = (overrides: Partial<FormValues> & { settings?: Record<string, string> } = {}, scenario: FormValues["scenario"] = "payments_errors"): FormValues => {
  const base = defaultForm(scenario);
  return { ...base, ...overrides, settings: { ...base.settings, ...overrides.settings } };
};

describe("defaults", () => {
  it("start from the contract: F1 8 s hold, F2 90% and 503, F3 1.5 s, 120 s", () => {
    expect(defaultForm("db_connection_exhaustion")).toEqual({ scenario: "db_connection_exhaustion", settings: { hold_seconds: "8" }, duration: "120" });
    expect(defaultForm("payments_errors")).toEqual({ scenario: "payments_errors", settings: { failure_rate: "90", status_code: "503" }, duration: "120" });
    expect(defaultForm("bad_deployment")).toEqual({ scenario: "bad_deployment", settings: { delay_seconds: "1.5" }, duration: "120" });
  });
});

describe("the request that is sent", () => {
  it("F2: percent becomes the ratio, the status code is a number, duration in seconds", () => {
    expect(validateForm(form({ duration: "300" }))).toEqual({
      ok: true,
      request: { scenario: "payments_errors", duration_seconds: 300, parameters: { failure_rate: 0.9, status_code: 503 } },
    });
  });

  it("F1 and F3 send their one setting, never the fixed release", () => {
    expect(validateForm(form({}, "db_connection_exhaustion"))).toEqual({ ok: true, request: { scenario: "db_connection_exhaustion", duration_seconds: 120, parameters: { hold_seconds: 8 } } });
    expect(validateForm(form({}, "bad_deployment"))).toEqual({ ok: true, request: { scenario: "bad_deployment", duration_seconds: 120, parameters: { delay_seconds: 1.5 } } });
  });

  it("402 is sent when chosen, and a percent with decimals becomes an exact ratio", () => {
    const result = validateForm(form({ settings: { failure_rate: "33.33", status_code: "402" } }));
    expect(result).toMatchObject({ ok: true, request: { parameters: { failure_rate: 0.3333, status_code: 402 } } });
  });

  it("the edges of every range are accepted", () => {
    expect(validateForm(form({ settings: { failure_rate: "0" }, duration: "0.1" })).ok).toBe(true);
    expect(validateForm(form({ settings: { failure_rate: "100" }, duration: "1800" })).ok).toBe(true);
    expect(validateForm(form({ settings: { hold_seconds: "0.1" } }, "db_connection_exhaustion")).ok).toBe(true);
    expect(validateForm(form({ settings: { hold_seconds: "15" } }, "db_connection_exhaustion")).ok).toBe(true);
    expect(validateForm(form({ settings: { delay_seconds: "1" } }, "bad_deployment")).ok).toBe(true);
    expect(validateForm(form({ settings: { delay_seconds: "2" } }, "bad_deployment")).ok).toBe(true);
  });
});

describe("what is refused before anything is sent", () => {
  it.each([
    ["failure rate above 100", { settings: { failure_rate: "100.01" } }, "failure_rate", "Enter 0–100 %."],
    ["failure rate negative", { settings: { failure_rate: "-1" } }, "failure_rate", "Enter 0–100 %."],
    ["failure rate empty", { settings: { failure_rate: "" } }, "failure_rate", "Enter 0–100 %."],
    ["failure rate not a number", { settings: { failure_rate: "abc" } }, "failure_rate", "Enter 0–100 %."],
    ["failure rate with three decimals", { settings: { failure_rate: "33.333" } }, "failure_rate", "Enter 0–100 %."],
    ["duration below 0.1 s", { duration: "0.09" }, "duration", "Enter 0.1–1800 s."],
    ["duration above 1800 s", { duration: "1801" }, "duration", "Enter 0.1–1800 s."],
    ["duration in exponent form", { duration: "1e2" }, "duration", "Enter 0.1–1800 s."],
    ["duration with a comma", { duration: "1,5" }, "duration", "Enter 0.1–1800 s."],
    ["duration empty", { duration: "  " }, "duration", "Enter 0.1–1800 s."],
  ])("%s", (_name, overrides, field, message) => {
    expect(validateForm(form(overrides as Partial<FormValues>))).toEqual({ ok: false, errors: { [field]: message } });
  });

  it("F1 hold and F3 delay outside their ranges", () => {
    expect(validateForm(form({ settings: { hold_seconds: "15.1" } }, "db_connection_exhaustion"))).toEqual({ ok: false, errors: { hold_seconds: "Enter 0.1–15 s." } });
    expect(validateForm(form({ settings: { hold_seconds: "0.05" } }, "db_connection_exhaustion"))).toEqual({ ok: false, errors: { hold_seconds: "Enter 0.1–15 s." } });
    expect(validateForm(form({ settings: { delay_seconds: "0.9" } }, "bad_deployment"))).toEqual({ ok: false, errors: { delay_seconds: "Enter 1–2 s." } });
    expect(validateForm(form({ settings: { delay_seconds: "2.1" } }, "bad_deployment"))).toEqual({ ok: false, errors: { delay_seconds: "Enter 1–2 s." } });
  });

  it("an unsupported status code is refused", () => {
    expect(validateForm(form({ settings: { status_code: "500" } }))).toEqual({ ok: false, errors: { status_code: "Choose 402 or 503." } });
  });

  it("every wrong field is reported at once", () => {
    const result = validateForm(form({ settings: { failure_rate: "x" }, duration: "0" }));
    expect(result).toEqual({ ok: false, errors: { failure_rate: "Enter 0–100 %.", duration: "Enter 0.1–1800 s." } });
  });
});

describe("the duration note", () => {
  it.each([
    ["300", "= 5 min"],
    ["120", "= 2 min"],
    ["150", "= 2.5 min"],
    ["45", "= 45 s"],
    ["0.1", "= 0.1 s"],
    ["1800", "= 30 min"],
    ["60", "= 1 min"],
  ])("%s s reads %s", (text, note) => {
    expect(durationNote(text)).toBe(note);
  });

  it.each(["", "abc", "0", "1801", "-5"])("%j has no note", (text) => {
    expect(durationNote(text)).toBeNull();
  });
});

describe("showing the fault that is running", () => {
  it("rebuilds the form from the controller's active incident (F2 90%, 503, 300 s)", () => {
    const active = controllerStatus("manual").active;
    expect(active).not.toBeNull();
    expect(formFromActive(active!)).toEqual({ scenario: "payments_errors", settings: { failure_rate: "90", status_code: "503" }, duration: "300" });
  });

  it("fills a setting the controller was not sent with the default it applies", () => {
    const active = { ...controllerStatus("manual").active!, scenario: "db_connection_exhaustion" as const, parameters: {} };
    expect(formFromActive(active)).toEqual({ scenario: "db_connection_exhaustion", settings: { hold_seconds: "8" }, duration: "300" });
  });

  it("is null for a scenario this page does not know", () => {
    const active = { ...controllerStatus("manual").active!, scenario: "future_fault" as never };
    expect(formFromActive(active)).toBeNull();
  });

  it("uses the controller's clock for the duration, never the page's", () => {
    const active = { ...controllerStatus("manual").active!, expires_at: CONTROLLER_NOW + 222 + 60 };
    expect(formFromActive(active)?.duration).toBe("360");
  });
});

describe("durations that are not whole seconds (review)", () => {
  it("keeps the controller's decimals up to two places", () => {
    const base = controllerStatus("manual").active!;
    const started = Date.parse(base.started_at) / 1000;
    expect(formFromActive({ ...base, expires_at: started + 300.25 })?.duration).toBe("300.25");
    expect(formFromActive({ ...base, expires_at: started + 0.1 })?.duration).toBe("0.1");
    expect(formFromActive({ ...base, expires_at: started + 90.5 })?.duration).toBe("90.5");
  });

  it("a whole number of seconds (within clock noise) shows as whole", () => {
    const base = controllerStatus("manual").active!;
    const started = Date.parse(base.started_at) / 1000;
    expect(formFromActive({ ...base, expires_at: started + 120.004 })?.duration).toBe("120");
  });
});
