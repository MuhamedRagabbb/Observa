// @vitest-environment node
// Checks the files that ship: dist/ is rebuilt in production mode by test/build-bundles.ts.
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const BUNDLES = ["store", "operator"] as const;
const FAMILIES = ["instrument-serif", "inter", "jetbrains-mono"];

const dist = (bundle: string, path = "") => join(import.meta.dirname, "..", "dist", bundle, path);
const read = (bundle: string, path: string) => readFileSync(dist(bundle, path), "utf8");
const assets = (bundle: string) => readdirSync(dist(bundle, "assets"));
const css = (bundle: string) =>
  assets(bundle)
    .filter((file) => file.endsWith(".css"))
    .map((file) => read(bundle, `assets/${file}`))
    .join("\n");

describe.each(BUNDLES)("%s bundle", (bundle) => {
  it("is an English, left-to-right document whose files load from /ui/next/", () => {
    const html = read(bundle, "index.html");
    expect(html).toMatch(/<html lang="en" dir="ltr">/);
    const refs = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((match) => match[1]);
    expect(refs.length).toBeGreaterThan(0);
    for (const ref of refs) expect(ref).toMatch(/^\/ui\/next\/assets\//);
  });

  it("self-hosts only the Latin files of the three approved font families", () => {
    const fonts = assets(bundle).filter((file) => /\.(woff2?|ttf|otf)$/.test(file));
    for (const family of FAMILIES) {
      expect(fonts.some((file) => file.startsWith(`${family}-latin-`) && file.endsWith(".woff2"))).toBe(true);
    }
    expect(fonts.every((file) => file.endsWith(".woff2"))).toBe(true);
    expect(fonts.some((file) => /latin-ext|cyrillic|greek|vietnamese/.test(file))).toBe(false);
    expect(fonts.some((file) => file.includes("italic"))).toBe(false);
  });

  it("declares every font face with swap and same-origin sources", () => {
    const styles = css(bundle);
    const faces = styles.match(/@font-face\{[^}]*\}/g) ?? [];
    expect(faces).toHaveLength(FAMILIES.length);
    for (const face of faces) expect(face).toContain("font-display:swap");
    for (const [, url] of styles.matchAll(/url\(["']?([^"')]+)/g)) {
      expect(url).toMatch(/^\/ui\/next\/assets\//);
    }
  });

  it("never loads anything from a third-party origin", () => {
    const shipped = [read(bundle, "index.html"), css(bundle)].join("\n");
    expect(shipped).not.toMatch(/https?:\/\//);
    expect(shipped).not.toMatch(/@import/);
    const scripts = assets(bundle)
      .filter((file) => file.endsWith(".js"))
      .map((file) => read(bundle, `assets/${file}`))
      .join("\n");
    expect(scripts).not.toMatch(/fonts\.(googleapis|gstatic)\.com|cdn\.|unpkg\.com|jsdelivr/);
  });

  it("ships the SIL Open Font License beside the fonts", () => {
    for (const family of FAMILIES) {
      expect(read(bundle, `licenses/${family}-OFL.txt`)).toContain("SIL Open Font License, Version 1.1");
    }
  });

  it("sets the page in the artboard palette and the approved type families", () => {
    const styles = css(bundle);
    // Pixel values sampled from the artboards (see src/shared/tokens.css).
    expect(styles).toContain("--color-paper:#f6f6f3");
    expect(styles).toContain("--color-ink:#151614");
    expect(styles).toMatch(/--font-display:"Instrument Serif"/);
    expect(styles).toMatch(/--font-ui:"Inter Variable"/);
    expect(styles).toMatch(/--font-mono:"JetBrains Mono Variable"/);
    expect(styles).toMatch(/body\{[^}]*background:var\(--color-paper\)/);
    expect(styles).toMatch(/body\{[^}]*font-family:var\(--font-ui\)/);
  });

  it("removes all motion when the visitor prefers reduced motion", () => {
    const styles = css(bundle);
    const durations = [...new Set([...styles.matchAll(/(--motion-duration-[a-z]+):/g)].map((m) => m[1]))];
    expect(durations.length).toBeGreaterThan(0);
    const reduced = styles.match(/@media \(prefers-reduced-motion:reduce\)\{(.*?\})\}/s)?.[1] ?? "";
    for (const name of durations) expect(reduced).toContain(`${name}:0s`);
    // Also stops motion that does not use the tokens, such as browser smooth scrolling.
    expect(reduced).toMatch(/scroll-behavior:auto!important/);
    expect(reduced).toMatch(/animation-duration:0s!important/);
    expect(reduced).toMatch(/transition-duration:0s!important/);
  });
});

describe("bundle isolation in the built output", () => {
  const scripts = (bundle: string) =>
    assets(bundle)
      .filter((file) => file.endsWith(".js"))
      .map((file) => read(bundle, `assets/${file}`))
      .join("\n");

  it("keeps operator code out of the store bundle", () => {
    // The minifier may quote strings with ", ' or `.
    const operatorLabel = /["'`]Operator["'`]/;
    expect(scripts("operator")).toMatch(operatorLabel);
    expect(scripts("store")).not.toMatch(operatorLabel);
  });
});
