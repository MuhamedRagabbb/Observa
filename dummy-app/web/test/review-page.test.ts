// @vitest-environment node
// The review page puts each captured state next to its approved artboard, per width.
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { CoverageState } from "../e2e/coverage.ts";
import { buildReview } from "../e2e/review/build-review.ts";

const PNG = Buffer.from("89504e470d0a1a0a", "hex");

const states: CoverageState[] = [
  {
    id: "cart-unavailable",
    slice: "P4-C",
    screen: "Cart <with> an unavailable line",
    approved: { "desktop-1440": "Cart drawer — desktop, with unavailable item.png", "mobile-375": "Cart sheet — 375 mobile.png" },
    derived: ["mobile-390"],
  },
  { id: "search-results", slice: "P4-G", screen: "Search open", approved: {}, derived: ["desktop-1440", "mobile-390", "mobile-375"] },
];

function setup() {
  const root = mkdtempSync(join(tmpdir(), "review-"));
  const screens = join(root, "screens");
  const artboards = join(root, "artboards");
  for (const dir of ["desktop-1440", "mobile-390"]) mkdirSync(join(screens, dir), { recursive: true });
  mkdirSync(artboards);
  writeFileSync(join(screens, "desktop-1440", "cart-unavailable.png"), PNG);
  writeFileSync(join(screens, "mobile-390", "cart-unavailable.png"), PNG);
  writeFileSync(join(artboards, "Cart drawer — desktop, with unavailable item.png"), PNG);
  return { root, screens, artboards, out: join(root, "out") };
}

describe("review page", () => {
  it("pairs each capture with its artboard and copies both next to the page", () => {
    const dirs = setup();
    const summary = buildReview({ states, screensDir: dirs.screens, artboardsDir: dirs.artboards, outDir: dirs.out });
    const html = readFileSync(join(dirs.out, "index.html"), "utf8");
    expect(html).toContain('src="screens/desktop-1440/cart-unavailable.png"');
    expect(html).toContain(`src="artboards/${encodeURIComponent("Cart drawer — desktop, with unavailable item.png")}"`);
    expect(existsSync(join(dirs.out, "screens", "desktop-1440", "cart-unavailable.png"))).toBe(true);
    expect(existsSync(join(dirs.out, "artboards", "Cart drawer — desktop, with unavailable item.png"))).toBe(true);
    expect(summary).toMatchObject({ cells: 6, captured: 2, approvedCells: 2, missingArtboards: ["Cart sheet — 375 mobile.png"] });
  });

  it("labels derived widths and shows the nearest approved artboard for reference", () => {
    const dirs = setup();
    buildReview({ states, screensDir: dirs.screens, artboardsDir: dirs.artboards, outDir: dirs.out });
    const html = readFileSync(join(dirs.out, "index.html"), "utf8");
    const derived390 = html.split('data-cell="cart-unavailable/mobile-390"')[1]?.split("</section>")[0] ?? "";
    expect(derived390).toContain("Derived");
    // The nearest approved width is the 375 sheet (missing in this fixture, so named, not shown).
    expect(derived390).toContain("Cart sheet — 375 mobile.png");
    const derived1440 = html.split('data-cell="search-results/desktop-1440"')[1]?.split("</section>")[0] ?? "";
    expect(derived1440).toContain("No artboard for this state");
  });

  it("prefers a state's own reference artboard for a derived width", () => {
    const dirs = setup();
    const withReference = [
      { ...states[1], reference: { "mobile-390": "Cart drawer — desktop, with unavailable item.png" } },
    ] as typeof states;
    buildReview({ states: withReference, screensDir: dirs.screens, artboardsDir: dirs.artboards, outDir: dirs.out });
    const html = readFileSync(join(dirs.out, "index.html"), "utf8");
    const cell = html.split('data-cell="search-results/mobile-390"')[1]?.split("</section>")[0] ?? "";
    expect(cell).toContain("Reference: Cart drawer — desktop, with unavailable item.png");
  });

  it("marks states not captured yet and artboards missing locally, and escapes text", () => {
    const dirs = setup();
    buildReview({ states, screensDir: dirs.screens, artboardsDir: dirs.artboards, outDir: dirs.out });
    const html = readFileSync(join(dirs.out, "index.html"), "utf8");
    expect(html).toContain("Not captured yet");
    expect(html).toContain("Artboard not found locally");
    expect(html).toContain("Cart &lt;with&gt; an unavailable line");
    expect(html).not.toContain("Cart <with>");
  });
});
