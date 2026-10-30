// @vitest-environment node
// /ui/config is outside OpenAPI, so its type is written by hand. contracts/ui-config.json
// is the shared contract: pytest checks the real Gateway and controller responses against
// it, and this test checks the hand-written type and parser against it.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseUiConfig, type UiConfig } from "../src/shared/ui-config.ts";

const contract: unknown = JSON.parse(
  readFileSync(join(import.meta.dirname, "..", "contracts", "ui-config.json"), "utf8"),
);

describe("/ui/config contract", () => {
  it("accepts the shared contract unchanged", () => {
    const config: UiConfig = parseUiConfig(contract);
    expect(config).toEqual(contract);
  });

  it("accepts the controller's mode", () => {
    expect(parseUiConfig({ ...(contract as object), mode: "control" }).mode).toBe("control");
  });

  it.each([
    ["an unknown mode", { mode: "admin" }],
    ["a missing port", { ports: { store: 8000, control: 8090, grafana: 3000, jaeger: 16686 } }],
    ["a port that is not a TCP port", { ports: { store: 8000, control: "8090", grafana: 3000, jaeger: 16686, prometheus: 9090 } }],
    ["an out-of-range port", { ports: { store: 8000, control: 70000, grafana: 3000, jaeger: 16686, prometheus: 9090 } }],
  ])("rejects %s", (_, change) => {
    expect(() => parseUiConfig({ ...(contract as object), ...change })).toThrow(/ui\/config/);
  });

  it("rejects a response that is not an object", () => {
    expect(() => parseUiConfig(null)).toThrow(/ui\/config/);
  });
});
