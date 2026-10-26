// @vitest-environment node
// The committed API types must equal a fresh generation from the committed OpenAPI
// snapshots (which a pytest test in turn keeps equal to the backend code).
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { API_NAMES, generate } from "../scripts/api-codegen.ts";

const committed = (name: string) =>
  readFileSync(join(import.meta.dirname, "..", "src", "api", "generated", `${name}.ts`), "utf8").replace(/\r\n/g, "\n");

describe.each(API_NAMES)("%s API types", (name) => {
  it("match a fresh generation from openapi/" + name + ".json", async () => {
    expect(committed(name)).toBe(await generate(name));
  });
});
