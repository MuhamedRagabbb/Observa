// @vitest-environment node
// P5-F: Playwright runs in one of three modes, never two at once: mocked (default), stack (a
// running preview project) or contract (the local harness around the real controller). Each mode
// runs only its own specs, so a contract or stack spec can never run in the default mocked suite.
import { describe, expect, it } from "vitest";
import { contractLauncher, contractRunEnvironment } from "../e2e/support/contract-server.ts";
import { TEST_MATCH, testMode } from "../e2e/support/modes.ts";

describe("test mode", () => {
  it("is mocked by default, stack with E2E_STACK_URL and contract with E2E_CONTRACT=1", () => {
    expect(testMode({})).toBe("mocked");
    expect(testMode({ E2E_CONTRACT: "" })).toBe("mocked");
    expect(testMode({ E2E_STACK_URL: "http://127.0.0.1:18000" })).toBe("stack");
    expect(testMode({ E2E_CONTRACT: "1" })).toBe("contract");
  });

  it("refuses two modes at once", () => {
    expect(() => testMode({ E2E_CONTRACT: "1", E2E_STACK_URL: "http://127.0.0.1:18000" })).toThrow(/one mode/);
  });

  it.each(["0", "true", "yes", "1 ", "2"])("refuses E2E_CONTRACT=%j (only 1 turns it on)", (value) => {
    expect(() => testMode({ E2E_CONTRACT: value })).toThrow(/E2E_CONTRACT must be 1/);
  });
});

describe("which specs each mode runs", () => {
  const files = {
    mocked: "D:\\Observa\\dummy-app\\web\\e2e\\operator.spec.ts",
    stack: "D:\\Observa\\dummy-app\\web\\e2e\\operator.stack.spec.ts",
    contract: "D:\\Observa\\dummy-app\\web\\e2e\\operator.contract.spec.ts",
    posix: "/work/web/e2e/checkout.spec.ts",
    helper: "D:\\Observa\\dummy-app\\web\\e2e\\support\\contract.ts",
  };

  it("mocked runs everything except stack and contract specs", () => {
    expect(TEST_MATCH.mocked.test(files.mocked)).toBe(true);
    expect(TEST_MATCH.mocked.test(files.posix)).toBe(true);
    expect(TEST_MATCH.mocked.test(files.stack)).toBe(false);
    expect(TEST_MATCH.mocked.test(files.contract)).toBe(false);
    expect(TEST_MATCH.mocked.test(files.helper)).toBe(false);
  });

  it("stack runs only stack specs, contract only contract specs", () => {
    expect([files.mocked, files.stack, files.contract].map((file) => TEST_MATCH.stack.test(file))).toEqual([false, true, false]);
    expect([files.mocked, files.stack, files.contract].map((file) => TEST_MATCH.contract.test(file))).toEqual([false, false, true]);
  });
});

describe("the contract launcher (Playwright webServer)", () => {
  const targets = { controller: "http://127.0.0.1:18190", control: "http://127.0.0.1:18191" };
  const root = "D:\\Observa\\dummy-app";
  const make = (over: Partial<Parameters<typeof contractLauncher>[0]> = {}) =>
    contractLauncher({ root, python: `${root}\\.venv\\Scripts\\python.exe`, targets, exists: () => true, ...over });

  it("starts the harness module in the repo root", () => {
    const launcher = make();
    expect(launcher.command).toContain("tests.harness.operator_contract");
    expect(launcher.command).toContain(`${root}\\.venv\\Scripts\\python.exe`);
    expect(launcher.cwd).toBe(root);
  });

  it("waits for the harness's own ready line and never probes a URL or port (a probe follows redirects and honours proxies)", () => {
    const launcher = make() as unknown as { url?: string; port?: number; wait?: { stdout: RegExp } };
    expect(launcher.url).toBeUndefined();
    expect(launcher.port).toBeUndefined();
    const ready = launcher.wait?.stdout;
    expect(ready).toBeInstanceOf(RegExp);
    expect(ready?.test("contract-harness ready controller=http://127.0.0.1:18190 control=http://127.0.0.1:18191")).toBe(true);
    for (const other of [
      "contract-harness ready controller=http://127.0.0.1:18090 control=http://127.0.0.1:18191",
      "contract-harness ready controller=http://127.0.0.1:18190 control=http://127.0.0.1:8090",
      "contract-harness ready controller=http://127.0.0.1:181900 control=http://127.0.0.1:18191",
      "contract-harness refused: PROMETHEUS_URL must be absent from the harness environment",
      "Uvicorn running on http://127.0.0.1:18190",
    ]) expect(ready?.test(other), other).toBe(false);
  });

  it("is bounded and never reuses a server that is already there", () => {
    const launcher = make();
    expect(launcher.reuseExistingServer).toBe(false);
    expect(launcher.timeout).toBeGreaterThan(0);
    expect(launcher.timeout).toBeLessThanOrEqual(60_000);
  });

  it("passes the two ports explicitly and nothing else (the harness refuses a missing port)", () => {
    expect(make().env).toEqual({ CONTRACT_CONTROLLER_PORT: "18190", CONTRACT_CONTROL_PORT: "18191" });
  });

  it("fails closed when the repo's Python is missing, instead of falling back to another one", () => {
    expect(() => make({ exists: () => false })).toThrow(/python/i);
  });
});

describe("the contract run environment (scripts/e2e-contract.ts)", () => {
  it("turns contract mode on and names the harness origins explicitly, here and nowhere else", () => {
    expect(contractRunEnvironment({ PATH: "x" })).toMatchObject({
      PATH: "x",
      E2E_CONTRACT: "1",
      E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:18190",
      E2E_CONTRACT_CONTROL_URL: "http://127.0.0.1:18191",
    });
  });

  it("keeps an operator-provided URL so that contractTarget can refuse it", () => {
    expect(contractRunEnvironment({ E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:8090" }).E2E_CONTRACT_CONTROLLER_URL).toBe("http://127.0.0.1:8090");
  });
});
