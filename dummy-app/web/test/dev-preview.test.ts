// @vitest-environment node
// `vite dev` proxies API calls to a preview project. It must never default to, or accept, the
// live stack's ports: a dev preview against live would create real orders and faults.
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { bundleConfig } from "../vite.bundle.ts";

type Proxy = Record<string, { target: string; changeOrigin?: boolean }>;
const proxy = (name: "store" | "operator", env: Record<string, string> = {}) =>
  bundleConfig(name, env).server?.proxy as Proxy;

describe("dev preview server", () => {
  it("serves on loopback ports of its own", () => {
    expect(bundleConfig("store", {}).server).toMatchObject({ host: "127.0.0.1", port: 5180, strictPort: true });
    expect(bundleConfig("operator", {}).server).toMatchObject({ host: "127.0.0.1", port: 5181, strictPort: true });
  });

  it("sends the store's API calls to the preview gateway by default", () => {
    const store = proxy("store");
    expect(Object.keys(store).sort()).toEqual(["/api", "/ui/config"]);
    for (const route of Object.values(store)) expect(route.target).toBe("http://127.0.0.1:18000");
  });

  it("sends the operator's calls to the preview controller and nothing to the gateway API", () => {
    const operator = proxy("operator");
    expect(Object.keys(operator).sort()).toEqual(["/activity", "/faults", "/services/health", "/ui/config"]);
    for (const route of Object.values(operator)) expect(route.target).toBe("http://127.0.0.1:18090");
  });

  it("follows OBSERVA_GATEWAY_URL and OBSERVA_CONTROLLER_URL", () => {
    expect(proxy("store", { OBSERVA_GATEWAY_URL: "http://127.0.0.1:28000" })["/api"]?.target).toBe("http://127.0.0.1:28000");
    expect(proxy("operator", { OBSERVA_CONTROLLER_URL: "http://127.0.0.1:28090" })["/faults"]?.target).toBe(
      "http://127.0.0.1:28090",
    );
  });

  it("refuses the live stack's ports", () => {
    expect(() => bundleConfig("store", { OBSERVA_GATEWAY_URL: "http://localhost:8000" })).toThrow(/live/);
    expect(() => bundleConfig("operator", { OBSERVA_CONTROLLER_URL: "http://127.0.0.1:8090/" })).toThrow(/live/);
  });
});

describe("preview and stack-mode guards", () => {
  it("serves `vite preview` (used by the mocked browser tests) without any proxy", () => {
    expect(bundleConfig("store", {}).preview?.proxy).toEqual({});
    expect(bundleConfig("operator", {}).preview?.proxy).toEqual({});
  });

  it("refuses to run stack-mode browser tests against the live gateway", () => {
    const run = spawnSync("npx", ["playwright", "test", "--list"], {
      cwd: join(import.meta.dirname, ".."),
      env: { ...process.env, E2E_STACK_URL: "http://127.0.0.1:8000/ui/next/" },
      encoding: "utf8",
      shell: true,
    });
    expect(run.status).not.toBe(0);
    expect(`${run.stdout}${run.stderr}`).toMatch(/E2E_STACK_URL=.*live stack port/);
  }, 60_000);
});
