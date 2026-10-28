// @vitest-environment node
// P5-0: every approved Operator artboard is mapped to an Operator Dashboard state, and every state
// is captured at all three review widths, against its artboard or derived (audit item 2: only
// idle and fault active have phone artboards; the rest are derived and tested).
import { describe, expect, it } from "vitest";
import { ALL_COVERAGE, COVERAGE, OPERATOR_COVERAGE, OPERATOR_SLICES, VIEWPORTS, type Viewport } from "../e2e/coverage.ts";

// The 11 Operator artboards in docs/design/artboards/, written out as the independent source.
const OPERATOR_ARTBOARDS = [
  "Operator — idle, 1440 desktop.png",
  "Operator — F1 Database Exhaustion config.png",
  "Operator — F2 Payment Errors config.png",
  "Operator — F3 Bad Deployment config.png",
  "Operator — manual fault active.png",
  "Operator — automatic cycle.png",
  "Operator — recovering.png",
  "Operator — fault status unknown.png",
  "Operator — controller disconnected.png",
  "Operator — 390 mobile, idle.png",
  "Operator — 390 mobile, fault active.png",
];
const viewports = Object.keys(VIEWPORTS) as Viewport[];

describe("operator coverage matrix", () => {
  it("maps each of the 11 approved Operator artboards exactly once", () => {
    const mapped = OPERATOR_COVERAGE.flatMap((state) => Object.values(state.approved));
    expect([...mapped].sort()).toEqual([...OPERATOR_ARTBOARDS].sort());
  });

  it("captures every state at 1440, 390 and 375, approved or derived but never both", () => {
    for (const state of OPERATOR_COVERAGE) {
      const approved = Object.keys(state.approved) as Viewport[];
      expect([...approved, ...state.derived].sort(), state.id).toEqual([...viewports].sort());
      expect(approved.filter((v) => state.derived.includes(v)), state.id).toEqual([]);
    }
  });

  it("puts the 390 phone artboards at 390 and every other artboard at 1440", () => {
    for (const state of OPERATOR_COVERAGE) {
      for (const [viewport, file] of Object.entries(state.approved)) {
        expect(viewport, `${state.id}: ${file}`).toBe(file.includes("390 mobile") ? "mobile-390" : "desktop-1440");
      }
    }
  });

  it("gives every state a unique operator- id and a P5 slice, distinct from the storefront's", () => {
    const ids = OPERATOR_COVERAGE.map((state) => state.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const state of OPERATOR_COVERAGE) {
      expect(state.id).toMatch(/^operator(-[a-z0-9]+)+$/);
      expect(OPERATOR_SLICES).toContain(state.slice);
    }
    expect(COVERAGE.filter((state) => ids.includes(state.id))).toEqual([]);
    expect(ALL_COVERAGE.map((state) => state.id)).toEqual([...COVERAGE, ...OPERATOR_COVERAGE].map((state) => state.id));
  });

  it("points derived widths only at Operator artboards, never at storefront ones", () => {
    for (const state of OPERATOR_COVERAGE) {
      for (const [viewport, file] of Object.entries(state.reference ?? {})) {
        expect(state.derived, `${state.id}: reference at an approved width`).toContain(viewport);
        expect(OPERATOR_ARTBOARDS, `${state.id}: ${file}`).toContain(file);
      }
      for (const viewport of state.derived) expect(state.reference?.[viewport], `${state.id} at ${viewport}`).toBeDefined();
    }
  });

  it("covers the six controller states the plan names", () => {
    expect(OPERATOR_COVERAGE.map((state) => state.id)).toEqual(
      expect.arrayContaining(["operator-idle", "operator-manual-active", "operator-automatic-cycle", "operator-recovering", "operator-status-unknown", "operator-disconnected"]),
    );
  });
});
