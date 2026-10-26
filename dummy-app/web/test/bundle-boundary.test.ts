// @vitest-environment node
// The store and operator bundles stay isolated at the source level: neither may import
// the other, and shared code may import neither. (P3-B adds the API-client rules.)
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(import.meta.dirname, "..", "src");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(tsx?|css|html)$/.test(entry.name) ? [path] : [];
  });
}

/** Every relative import in `area`, as the top-level src folder it resolves into. */
function importedAreas(area: string) {
  return sourceFiles(join(SRC, area)).flatMap((file) => {
    const text = readFileSync(file, "utf8");
    const specs = [
      ...text.matchAll(/(?:import|export)[^"']*?from\s*["']([^"']+)["']/g),
      ...text.matchAll(/import\s*\(?\s*["']([^"']+)["']/g),
      ...text.matchAll(/@import\s+["']([^"']+)["']/g),
      ...text.matchAll(/src="([^"]+)"/g),
    ].map((match) => match[1] ?? "");
    return specs
      .filter((spec) => spec.startsWith("."))
      .map((spec) => ({
        file: relative(SRC, file),
        spec,
        area: relative(SRC, resolve(dirname(file), spec)).split(sep)[0],
      }));
  });
}

describe("bundle boundary", () => {
  it.each([
    ["store", "operator"],
    ["operator", "store"],
  ])("%s code never imports %s code", (area, forbidden) => {
    expect(importedAreas(area).filter((link) => link.area === forbidden)).toEqual([]);
  });

  it("shared code imports neither bundle", () => {
    const links = importedAreas("shared").filter((link) => link.area === "store" || link.area === "operator");
    expect(links).toEqual([]);
  });

  it("finds the imports it checks", () => {
    expect(importedAreas("store").some((link) => link.area === "shared")).toBe(true);
  });
});
