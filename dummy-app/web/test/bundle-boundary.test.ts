// @vitest-environment node
// The store and operator bundles stay isolated at the source level. Imports are followed
// transitively, so an indirect path (store -> shared -> controller client) is caught too.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { describe, expect, it } from "vitest";

const SRC = join(import.meta.dirname, "..", "src");
const CONTROLLER_API = ["api/controller-client.ts", "api/generated/controller.ts"];

const rel = (file: string) => relative(SRC, file).split(sep).join("/");

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(tsx?|css|html)$/.test(entry.name) ? [path] : [];
  });
}

/** Relative imports of one file, resolved to files under src/. */
function imports(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const specs = [
    ...text.matchAll(/(?:import|export)[^"';]*?from\s*["']([^"']+)["']/g),
    ...text.matchAll(/import\s*\(?\s*["']([^"']+)["']/g),
    ...text.matchAll(/@import\s+["']([^"']+)["']/g),
    ...text.matchAll(/src="([^"]+)"/g),
  ].map((match) => match[1] ?? "");
  return specs
    .filter((spec) => spec.startsWith("."))
    .map((spec) => resolve(dirname(file), spec))
    .map((path) => {
      if (!existsSync(path)) throw new Error(`${rel(file)} imports a missing file: ${path}`);
      return path;
    });
}

/** Every file reachable from `roots` through relative imports, as src-relative paths. */
function reachable(roots: string[]): Set<string> {
  const seen = new Set<string>();
  const queue = [...roots];
  while (queue.length > 0) {
    const file = queue.pop() as string;
    if (seen.has(rel(file))) continue;
    seen.add(rel(file));
    queue.push(...imports(file));
  }
  return seen;
}

const from = (...parts: string[]) => reachable(sourceFiles(join(SRC, ...parts)));
const inside = (files: Set<string>, prefix: string) => [...files].filter((file) => file.startsWith(prefix));

describe("bundle boundary", () => {
  it("store code never reaches operator code or the controller API", () => {
    const files = from("store");
    expect(inside(files, "operator/")).toEqual([]);
    expect(CONTROLLER_API.filter((file) => files.has(file))).toEqual([]);
  });

  it("the Gateway client never reaches the controller API", () => {
    const files = reachable([join(SRC, "api", "gateway-client.ts")]);
    expect(files.has("api/generated/gateway.ts")).toBe(true);
    expect(CONTROLLER_API.filter((file) => files.has(file))).toEqual([]);
  });

  it("neither bundle reaches test-only code (the gallery, fixtures) outside src/", () => {
    // The one exception: shared contract data in contracts/*.json, which pytest checks against the
    // backend (P5-0: the operator reads fault-parameters.json). Never code, never e2e/ or test/.
    const outside = [...from("store"), ...from("operator")].filter((file) => file.startsWith("../"));
    expect(outside.filter((file) => !/^\.\.\/contracts\/[a-z-]+\.json$/.test(file))).toEqual([]);
    expect(outside).toEqual(["../contracts/fault-parameters.json"]);
    expect([...from("store")].filter((file) => file.startsWith("../"))).toEqual([]);
  });

  it("operator code never reaches store code", () => {
    expect(inside(from("operator"), "store/")).toEqual([]);
  });

  it("shared code reaches neither bundle nor any API client", () => {
    const files = from("shared");
    expect([...inside(files, "store/"), ...inside(files, "operator/"), ...inside(files, "api/")]).toEqual([]);
  });

  it("follows imports through several files to shared code", () => {
    // store/routes -> store/StoreLayout -> store/StoreHeader -> shared/ui/Brand
    expect(from("store").has("shared/ui/Brand.tsx")).toBe(true);
    // operator/routes -> shared/Shell -> shared/ui/Brand
    expect(from("operator").has("shared/ui/Brand.tsx")).toBe(true);
  });
});
