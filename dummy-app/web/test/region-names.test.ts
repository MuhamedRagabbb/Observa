// @vitest-environment node
// P5-F T1: on phones each operator section is labelled by its heading, which holds the disclosure button, so
// the region's accessible name is the title FOLLOWED BY the summary ("Manual fault injection F2 · 90% ·
// HTTP 503 · 120 s", "... Locked · ..."); on desktop it is the title alone. The contract specs look a section
// up by a name that accepts exactly those two shapes and nothing else: an exact-name lookup finds the section
// only on desktop, and a bare substring would also match unrelated regions.
import { describe, expect, it } from "vitest";
import { sectionName } from "../e2e/support/region-names.ts";

describe("sectionName", () => {
  const manual = sectionName("Manual fault injection");
  const cycle = sectionName("Automatic cycle");

  it("matches the desktop name, the title alone", () => {
    expect(manual.test("Manual fault injection")).toBe(true);
    expect(cycle.test("Automatic cycle")).toBe(true);
  });

  it("matches the phone name, the title followed by the summary, locked or not", () => {
    for (const name of [
      "Manual fault injection F2 · 90% · HTTP 503 · 120 s",
      "Manual fault injection Locked · F2 · 90% · HTTP 503 · 120 s",
      "Manual fault injection F1 · 8 s · 120 s",
    ]) expect(manual.test(name)).toBe(true);
    for (const name of ["Automatic cycle 1 repeat · 12 min 30 s", "Automatic cycle Locked · 1 repeat · 12 min 30 s"]) expect(cycle.test(name)).toBe(true);
  });

  it("does not match another section, however similar", () => {
    for (const name of ["Experiment", "Run in progress", "Service health", "Session activity", "Planned enhancements", "Automatic cycle"]) {
      expect(manual.test(name)).toBe(false);
    }
    for (const name of ["Manual fault injection", "Stop automatic cycle", "Automatic cycles", "The Automatic cycle", "automatic cycle", "Planned automatic cycle"]) {
      expect(cycle.test(name)).toBe(false);
    }
  });

  it("is anchored: the title must start the name and end it or be followed by a space", () => {
    expect(manual.test("Manual fault injections")).toBe(false);
    expect(manual.test("Prior Manual fault injection")).toBe(false);
    expect(manual.test("manual fault injection")).toBe(false);
  });

  it("treats the title literally, not as a pattern", () => {
    expect(sectionName("a.c").test("abc")).toBe(false);
    expect(sectionName("a.c").test("a.c summary")).toBe(true);
    expect(sectionName("(x)+").test("(x)+")).toBe(true);
  });
});
