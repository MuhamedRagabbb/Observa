// @vitest-environment node
// P5-F: the controller's answers never carry ground truth (the injected fault's root cause, the
// service it is in, or the signals expected from it). Contract tests scan every API answer for it;
// this is the scanner. It reads decoded JSON keys and strings, never HTML, scripts or styles.
import { describe, expect, it } from "vitest";
import { groundTruthLeaks, shouldInspectJson } from "../e2e/support/ground-truth.ts";

describe("groundTruthLeaks", () => {
  it("finds nothing in an ordinary controller answer", () => {
    expect(groundTruthLeaks({ state: "manual", active: { scenario: "payments_errors", parameters: { failure_rate: 0.9 } }, controls: { activate: { enabled: false, reason: "fault_active" } } })).toEqual([]);
    expect(groundTruthLeaks([])).toEqual([]);
    expect(groundTruthLeaks(null)).toEqual([]);
    expect(groundTruthLeaks("hello")).toEqual([]);
  });

  it.each(["root_cause", "root_cause_service", "expected_signals"])("finds the key %s at any depth", (key) => {
    expect(groundTruthLeaks({ a: [{ b: { [key]: "x" } }] })).toEqual([`a[0].b.${key}`]);
    expect(groundTruthLeaks({ [key]: 1 })).toEqual([key]);
  });

  it("finds a key that was written with escapes, because it reads decoded JSON", () => {
    expect(groundTruthLeaks(JSON.parse('{"root_\\u0063ause":"x"}'))).toEqual(["root_cause"]);
  });

  it("finds a token inside a string value", () => {
    expect(groundTruthLeaks({ message: "see expected_signals for details" })).toEqual(["message"]);
  });

  it("reports every leak once, with its path", () => {
    expect(groundTruthLeaks({ one: { root_cause: 1 }, two: ["expected_signals"], three: { root_cause_service: 1 } }).sort()).toEqual(["one.root_cause", "three.root_cause_service", "two[0]"]);
  });

  it("does not choke on a cycle or a deep tree", () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(groundTruthLeaks(cyclic)).toEqual([]);
    let deep: unknown = { root_cause: 1 };
    for (let i = 0; i < 200; i++) deep = { next: deep };
    expect(groundTruthLeaks(deep)).toHaveLength(1);
  });
});

describe("shouldInspectJson (which responses the leak scan reads)", () => {
  it("reads ordinary JSON answers, successful or not", () => {
    expect(shouldInspectJson(200, "application/json")).toBe(true);
    expect(shouldInspectJson(200, "application/json; charset=utf-8")).toBe(true);
    expect(shouldInspectJson(409, "application/problem+json")).toBe(true);
    expect(shouldInspectJson(503, "application/json")).toBe(true);
    expect(shouldInspectJson(201, "application/vnd.api+json")).toBe(true);
  });

  it("never tries to read a redirect: Playwright cannot expose its body, and the attempt would be a false failure", () => {
    for (const status of [300, 301, 302, 303, 307, 308, 399]) expect(shouldInspectJson(status, "application/json")).toBe(false);
  });

  it("does not read what is not JSON", () => {
    for (const type of [undefined, "", "text/html", "application/javascript", "text/plain", "font/woff2", "application/octet-stream"]) {
      expect(shouldInspectJson(200, type)).toBe(false);
    }
  });
});
