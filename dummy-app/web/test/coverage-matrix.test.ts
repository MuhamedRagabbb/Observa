// @vitest-environment node
// Every approved storefront artboard must be mapped to a UI state, and every state must be
// captured at all three review widths, either against an artboard or as a derived variant.
import { describe, expect, it } from "vitest";
import { COVERAGE, SLICES, VIEWPORTS, type Viewport } from "../e2e/coverage.ts";

// The 28 storefront artboards listed in docs/design/OBSERVA_Design_Handoff_Audit.md
// (sections 01-04). Written out here as the independent source of truth.
const AUDIT_STOREFRONT_ARTBOARDS = [
  "Hero background — 16 9 desktop.png",
  "Product Collection — 1440 desktop.png",
  "Product Collection — 390 mobile.png",
  "Cart drawer — desktop, with unavailable item.png",
  "Cart drawer — desktop, empty.png",
  "Cart drawer — desktop, loading.png",
  "Cart drawer — desktop, error.png",
  "Cart sheet — 375 mobile.png",
  "Cart drawer — desktop, ready to check out.png",
  "Checkout — 1440 desktop.png",
  "Outcome 1 — Processing.png",
  "Outcome 2 — Success.png",
  "Outcome 3a — Payment failed, retryable.png",
  "Outcome 4 — Status unknown.png",
  "Checkout — 375 mobile.png",
  "Mobile — Processing.png",
  "Mobile — Success.png",
  "Mobile — Payment failed.png",
  "Mobile — Status unknown.png",
  "Outcome 3b — Failed, not retryable.png",
  "My Orders — list, 1440 desktop.png",
  "My Orders — order detail, confirmed.png",
  "My Orders — order detail, status unknown.png",
  "My Orders — empty.png",
  "My Orders — loading.png",
  "My Orders — error.png",
  "My Orders — 375 mobile list.png",
  "My Orders — 375 mobile, status unknown.png",
];

const viewports = Object.keys(VIEWPORTS) as Viewport[];

describe("storefront coverage matrix", () => {
  it("maps each of the 28 approved storefront artboards exactly once", () => {
    const mapped = COVERAGE.flatMap((state) => Object.values(state.approved));
    expect(mapped).toHaveLength(28);
    expect(new Set(mapped).size).toBe(28);
    expect([...mapped].sort()).toEqual([...AUDIT_STOREFRONT_ARTBOARDS].sort());
  });

  it("captures every state at 1440, 390 and 375, as approved or derived but never both", () => {
    expect(VIEWPORTS).toEqual({
      "desktop-1440": { width: 1440, height: 900 },
      "mobile-390": { width: 390, height: 844 },
      "mobile-375": { width: 375, height: 812 },
    });
    for (const state of COVERAGE) {
      const approved = Object.keys(state.approved) as Viewport[];
      expect([...approved, ...state.derived].sort(), state.id).toEqual([...viewports].sort());
      expect(approved.filter((v) => state.derived.includes(v)), state.id).toEqual([]);
    }
  });

  it("matches artboard widths to viewports: desktop artboards at 1440, mobile ones at 390 or 375", () => {
    for (const state of COVERAGE) {
      for (const [viewport, file] of Object.entries(state.approved)) {
        const mobile = /375|390|Mobile —/.test(file);
        expect(viewport.startsWith("mobile"), `${state.id}: ${file}`).toBe(mobile);
        if (file.includes("390")) expect(viewport).toBe("mobile-390");
        if (file.includes("375") || file.startsWith("Mobile —")) expect(viewport).toBe("mobile-375");
      }
    }
  });

  it("gives every state a unique kebab-case id and a known P4 slice", () => {
    const ids = COVERAGE.map((state) => state.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const state of COVERAGE) {
      expect(state.id).toMatch(/^[a-z0-9]+(-[a-z0-9]+)*$/);
      expect(SLICES).toContain(state.slice);
    }
  });

  it("only points derived widths at reference artboards, chosen from the 28 approved ones", () => {
    for (const state of COVERAGE) {
      for (const [viewport, file] of Object.entries(state.reference ?? {})) {
        expect(state.derived, `${state.id}: reference at an approved width`).toContain(viewport);
        expect(AUDIT_STOREFRONT_ARTBOARDS, `${state.id}: ${file}`).toContain(file);
      }
    }
    const cartSheet = "Cart sheet — 375 mobile.png";
    for (const id of ["cart-ready", "cart-empty", "cart-loading", "cart-error"]) {
      const state = COVERAGE.find((s) => s.id === id);
      expect(state?.reference?.["mobile-375"], id).toBe(cartSheet);
    }
  });

  it("covers the states the checkout safety rules add", () => {
    const ids = COVERAGE.map((s) => s.id);
    expect(ids).toEqual(
      expect.arrayContaining([
        "checkout-form-invalid",
        "checkout-unconfirmed-prompt",
        "outcome-still-processing",
        "orders-list-closed-attempts",
      ]),
    );
  });

  it("lists the derived-only states that have no artboard at any width", () => {
    const derivedOnly = COVERAGE.filter((state) => Object.keys(state.approved).length === 0).map((s) => s.id);
    expect(derivedOnly).toEqual(
      expect.arrayContaining(["search-results", "search-no-results", "outcome-abandoned", "order-detail-declined"]),
    );
  });
});
