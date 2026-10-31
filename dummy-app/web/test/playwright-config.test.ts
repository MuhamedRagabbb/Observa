// @vitest-environment node
// P5-F: the Playwright configuration in its three modes. This only imports the config and reads its
// shape; Playwright (and so every web server the config names) is never started.
import { existsSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const ROOT = join(import.meta.dirname, "..", "..");
const PYTHON = join(ROOT, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
const KEYS = ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "E2E_STACK_URL", "E2E_STACK_CONTROLLER_URL", "E2E_CONTRACT", "E2E_CONTRACT_CONTROLLER_URL", "E2E_CONTRACT_CONTROL_URL"];
const saved: Record<string, string | undefined> = {};

beforeEach(() => {
  for (const key of KEYS) {
    saved[key] = process.env[key];
    delete process.env[key];
  }
  vi.resetModules();
});
afterEach(() => {
  for (const key of KEYS) {
    if (saved[key] === undefined) delete process.env[key];
    else process.env[key] = saved[key];
  }
});

async function load(env: Record<string, string> = {}) {
  Object.assign(process.env, env);
  vi.resetModules();
  return (await import("../playwright.config.ts")).default;
}
const names = (config: Awaited<ReturnType<typeof load>>) => (config.projects ?? []).map((project) => project.name);

describe("mocked mode (the default) is unchanged", () => {
  it("runs the three viewports with two workers against the three vite previews", async () => {
    const config = await load();
    expect(names(config)).toEqual(["desktop-1440", "mobile-390", "mobile-375"]);
    expect(config.workers).toBe(2);
    expect(Array.isArray(config.webServer) ? config.webServer.map((server) => server.url) : []).toEqual([
      "http://127.0.0.1:4380/ui/next/",
      "http://127.0.0.1:4381/ui/next/",
      "http://127.0.0.1:4382/ui/next/",
    ]);
  });

  it("has no global teardown", async () => {
    expect((await load()).globalTeardown).toBeUndefined();
  });

  it("never picks up stack or contract specs", async () => {
    const [project] = (await load()).projects ?? [];
    expect(String(project?.testMatch)).not.toContain("contract.spec");
    expect((project?.testMatch as RegExp).test("e2e/operator.contract.spec.ts")).toBe(false);
    expect((project?.testMatch as RegExp).test("e2e/operator.stack.spec.ts")).toBe(false);
    expect((project?.testMatch as RegExp).test("e2e/operator.spec.ts")).toBe(true);
  });
});

describe("stack mode is unchanged", () => {
  it("runs only stack specs, one worker, no web servers, against the preview Gateway", async () => {
    const config = await load({ E2E_STACK_URL: "http://127.0.0.1:18000" });
    expect(names(config)).toEqual(["stack-desktop-1440", "stack-mobile-390", "stack-mobile-375"]);
    expect(config.workers).toBe(1);
    expect(config.webServer).toBeUndefined();
    expect(config.projects?.[0]?.use?.baseURL).toBe("http://127.0.0.1:18000/");
  });

  it("has no global teardown", async () => {
    expect((await load({ E2E_STACK_URL: "http://127.0.0.1:18000" })).globalTeardown).toBeUndefined();
  });

  it("still refuses a live port", async () => {
    await expect(load({ E2E_STACK_URL: "http://127.0.0.1:8002" })).rejects.toThrow(/live stack port/);
  });
});

const EXPLICIT = { E2E_CONTRACT: "1", E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:18190", E2E_CONTRACT_CONTROL_URL: "http://127.0.0.1:18191" };

describe.skipIf(!existsSync(PYTHON))("contract mode", () => {
  it("has no implicit origins: running the config directly without them fails closed", async () => {
    await expect(load({ E2E_CONTRACT: "1" })).rejects.toThrow(/E2E_CONTRACT_CONTROLLER_URL/);
    expect(process.env.E2E_CONTRACT_CONTROLLER_URL).toBeUndefined();
  });

  it("refuses an inherited proxy before Playwright could probe through it", async () => {
    await expect(load({ ...EXPLICIT, HTTP_PROXY: "http://127.0.0.1:8090" })).rejects.toThrow(/proxy/i);
  });

  it("asks the harness to stop cooperatively before Playwright force-kills it (Windows has no graceful stop)", async () => {
    const config = await load(EXPLICIT);
    expect(String(config.globalTeardown)).toMatch(/contract-global-teardown.ts$/);
  });

  it("bounds the whole run, including the readiness probe", async () => {
    const config = await load(EXPLICIT);
    expect(config.globalTimeout).toBeGreaterThan(0);
    expect(config.globalTimeout).toBeLessThanOrEqual(30 * 60_000);
  });

  it("runs only contract specs, one worker, with the harness as the only web server", async () => {
    const config = await load(EXPLICIT);
    expect(names(config)).toEqual(["contract-desktop-1440", "contract-mobile-390", "contract-mobile-375"]);
    expect(config.workers).toBe(1);
    expect(config.projects?.[0]?.use?.baseURL).toBe("http://127.0.0.1:18190/");
    const servers = Array.isArray(config.webServer) ? config.webServer : config.webServer ? [config.webServer] : [];
    expect(servers).toHaveLength(1);
    expect(servers[0]).not.toHaveProperty("url");
    expect(servers[0]).not.toHaveProperty("port");
    expect(servers[0]).toMatchObject({
      wait: { stdout: expect.any(RegExp) },
      reuseExistingServer: false,
      env: { CONTRACT_CONTROLLER_PORT: "18190", CONTRACT_CONTROL_PORT: "18191" },
    });
    expect(servers[0]?.command).toContain("tests.harness.operator_contract");
    const match = config.projects?.[0]?.testMatch as RegExp;
    expect([match.test("e2e/operator.contract.spec.ts"), match.test("e2e/operator.spec.ts"), match.test("e2e/operator.stack.spec.ts")]).toEqual([true, false, false]);
  });

  it("refuses to share a run with stack mode, and any live or preview port", async () => {
    await expect(load({ E2E_CONTRACT: "1", E2E_STACK_URL: "http://127.0.0.1:18000" })).rejects.toThrow(/one mode/);
    await expect(load({ E2E_CONTRACT: "1", E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:8090", E2E_CONTRACT_CONTROL_URL: "http://127.0.0.1:18191" })).rejects.toThrow(/live stack port/);
    await expect(load({ E2E_CONTRACT: "1", E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:18190", E2E_CONTRACT_CONTROL_URL: "http://127.0.0.1:18090" })).rejects.toThrow(/preview/);
  });
});
