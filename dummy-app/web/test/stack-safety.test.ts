// @vitest-environment node
// P5-0: stack-mode tests (storefront and, from P5-F, the Operator Dashboard) reach a running
// preview project's Gateway and controller. These checks decide where they may go: never the live
// stack, only loopback http (the controller's origin guard refuses anything else anyway), and the
// controller never on the Gateway's origin. In the browser, each request must stay on an allowed
// origin: the Gateway's by default, the controller's for operator tests.
import { describe, expect, it, vi } from "vitest";
import { allowedOrigin, contractTarget, stackTargets, t2PreviewTargets } from "../e2e/support/stack-safety.ts";
import ports from "../contracts/stack-ports.json" with { type: "json" };
import { PREVIEW_STACK_PORTS, refuseLive } from "../preview-guard.ts";

describe("live and preview ports (contracts/stack-ports.json)", () => {
  it("lists every live-published port, sorted and unique, apart from the preview's", () => {
    expect(ports.live).toEqual([...new Set(ports.live)].sort((a, b) => a - b));
    for (const port of [3000, 3100, 4317, 4318, 5432, 5433, 8000, 8001, 8002, 8003, 8090, 8889, 9090, 16686]) expect(ports.live, `${port}`).toContain(port);
    for (const port of Object.values(ports.preview)) expect(ports.live).not.toContain(port);
  });

  it.each(ports.live)("refuses live port %i everywhere a target is checked", (port) => {
    expect(() => refuseLive("TARGET", `http://127.0.0.1:${port}`)).toThrow(/live stack port/);
    expect(() => stackTargets({ E2E_STACK_URL: `http://127.0.0.1:${port}` })).toThrow(/live stack port/);
    expect(() => stackTargets({ E2E_STACK_URL: "http://127.0.0.1:18000", E2E_STACK_CONTROLLER_URL: `http://127.0.0.1:${port}` })).toThrow(/live stack port/);
  });

  it.each(Object.entries(ports.preview))("still allows the preview's %s port %i in stack mode", (_name, port) => {
    expect(refuseLive("TARGET", `http://127.0.0.1:${port}/`)).toBe(`http://127.0.0.1:${port}`);
  });
});

describe("contract target (the local harness around the real controller)", () => {
  const env = { E2E_CONTRACT: "1", E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:18190", E2E_CONTRACT_CONTROL_URL: "http://127.0.0.1:18191" };

  it("is off unless E2E_CONTRACT=1", () => {
    expect(contractTarget({})).toBeNull();
    expect(contractTarget({ E2E_CONTRACT: "" })).toBeNull();
  });

  it("returns the controller and test-control origins", () => {
    expect(contractTarget(env)).toEqual({ controller: "http://127.0.0.1:18190", control: "http://127.0.0.1:18191" });
    expect(contractTarget({ ...env, E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:18190/" })?.controller).toBe("http://127.0.0.1:18190");
  });

  it.each(["0", "true", "yes", "2"])("refuses E2E_CONTRACT=%j", (value) => {
    expect(() => contractTarget({ ...env, E2E_CONTRACT: value })).toThrow(/E2E_CONTRACT must be 1/);
  });

  it("has no implicit defaults: both URLs must be given", () => {
    expect(() => contractTarget({ E2E_CONTRACT: "1" })).toThrow(/E2E_CONTRACT_CONTROLLER_URL/);
    expect(() => contractTarget({ E2E_CONTRACT: "1", E2E_CONTRACT_CONTROLLER_URL: env.E2E_CONTRACT_CONTROLLER_URL })).toThrow(/E2E_CONTRACT_CONTROL_URL/);
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROL_URL: "" })).toThrow(/E2E_CONTRACT_CONTROL_URL/);
  });

  it.each(ports.live)("refuses live port %i for either URL", (port) => {
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROLLER_URL: `http://127.0.0.1:${port}` })).toThrow(/live stack port/);
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROL_URL: `http://127.0.0.1:${port}` })).toThrow(/live stack port/);
  });

  it.each(Object.values(ports.preview))("refuses the preview project's port %i (the harness never takes the preview's place)", (port) => {
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROLLER_URL: `http://127.0.0.1:${port}` })).toThrow(/preview/);
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROL_URL: `http://127.0.0.1:${port}` })).toThrow(/preview/);
  });

  it.each([
    ["localhost (it may resolve to ::1, which the harness does not bind)", "http://localhost:18190", /127\.0\.0\.1/],
    ["another host", "http://192.168.1.20:18190", /127\.0\.0\.1/],
    ["https", "https://127.0.0.1:18190", /http:/],
    ["a path", "http://127.0.0.1:18190/faults", /origin only/],
    ["a query", "http://127.0.0.1:18190?x=1", /origin only/],
    ["user info", "http://user@127.0.0.1:18190", /origin only/],
    ["a privileged port", "http://127.0.0.1:1023", /1024/],
    ["no port", "http://127.0.0.1", /port/],
    ["garbage", "not a url", /URL/],
  ])("refuses %s", (_name, url, message) => {
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROLLER_URL: url })).toThrow(message);
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROL_URL: url })).toThrow(message);
  });

  it("refuses both URLs on one origin", () => {
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROL_URL: env.E2E_CONTRACT_CONTROLLER_URL })).toThrow(/same origin/);
  });

  it.each(["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"])(
    "refuses an inherited %s: Playwright's readiness probe would send its request through it",
    (name) => {
      expect(() => contractTarget({ ...env, [name]: "http://127.0.0.1:8090" })).toThrow(/proxy/i);
    },
  );

  it("ignores an empty proxy variable and NO_PROXY", () => {
    expect(contractTarget({ ...env, HTTP_PROXY: "", NO_PROXY: "127.0.0.1" })).not.toBeNull();
  });

  it.each([
    ["a leading zero in the port", "http://127.0.0.1:018190", /port/],
    ["a trailing dot on the host", "http://127.0.0.1.:18190", /127\.0\.0\.1/],
    ["a shortened IPv4 host", "http://127.1:18190", /127\.0\.0\.1/],
    ["a hexadecimal IPv4 host", "http://0x7f.0.0.1:18190", /127\.0\.0\.1/],
    ["a decimal IPv4 host", "http://2130706433:18190", /127\.0\.0\.1/],
    ["an encoded path", "http://127.0.0.1:18190/%2e%2e", /origin only/],
    ["a port above 65535", "http://127.0.0.1:65536", /URL/],
    ["an IPv6 loopback", "http://[::1]:18190", /127\.0\.0\.1/],
  ])("refuses %s", (_name, url, message) => {
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROLLER_URL: url })).toThrow(message);
    expect(() => contractTarget({ ...env, E2E_CONTRACT_CONTROL_URL: url })).toThrow(message);
  });

  it("accepts the bounds of the allowed port range", () => {
    expect(contractTarget({ ...env, E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:1024" })?.controller).toBe("http://127.0.0.1:1024");
    expect(contractTarget({ ...env, E2E_CONTRACT_CONTROLLER_URL: "http://127.0.0.1:65535" })?.controller).toBe("http://127.0.0.1:65535");
  });

  it("refuses to run together with stack mode", () => {
    expect(() => contractTarget({ ...env, E2E_STACK_URL: "http://127.0.0.1:18000" })).toThrow(/E2E_STACK_URL/);
  });
});

describe("stack targets", () => {
  it("is off without E2E_STACK_URL", () => {
    expect(stackTargets({})).toBeNull();
  });

  it("defaults the controller to port 18090 on the Gateway's loopback host", () => {
    expect(stackTargets({ E2E_STACK_URL: "http://127.0.0.1:18000/" })).toEqual({ gateway: "http://127.0.0.1:18000", controller: "http://127.0.0.1:18090" });
    expect(stackTargets({ E2E_STACK_URL: "http://localhost:18000", E2E_STACK_CONTROLLER_URL: "http://localhost:28090" })).toEqual({
      gateway: "http://localhost:18000",
      controller: "http://localhost:28090",
    });
  });

  it.each([
    ["the live Gateway", { E2E_STACK_URL: "http://127.0.0.1:8000" }, /live stack port/],
    ["the live controller", { E2E_STACK_URL: "http://127.0.0.1:18000", E2E_STACK_CONTROLLER_URL: "http://localhost:8090" }, /live stack port/],
    ["a host that is not loopback", { E2E_STACK_URL: "http://192.168.1.20:18000" }, /loopback/],
    ["a controller that is not loopback", { E2E_STACK_URL: "http://127.0.0.1:18000", E2E_STACK_CONTROLLER_URL: "http://example.test:18090" }, /loopback/],
    ["https (the controller only accepts its own http origin)", { E2E_STACK_URL: "https://127.0.0.1:18000" }, /http:/],
    ["the controller on the Gateway's own origin", { E2E_STACK_URL: "http://127.0.0.1:18000", E2E_STACK_CONTROLLER_URL: "http://127.0.0.1:18000" }, /same origin/],
  ])("refuses %s", (_name, env, message) => {
    expect(() => stackTargets(env)).toThrow(message);
  });
});

describe("T2 preview targets (image-served operator only)", () => {
  const env = { E2E_STACK_URL: "http://127.0.0.1:18000", E2E_T2_EXCLUSIVE: "1" };
  const targets = { gateway: "http://127.0.0.1:18000", controller: "http://127.0.0.1:18090" };

  it.each([undefined, "", "0", "true", " 1", "yes"])(
    "requires the exact exclusive-preview attestation E2E_T2_EXCLUSIVE=1 (got %j)",
    (value) => {
      expect(() => t2PreviewTargets({ ...env, E2E_T2_EXCLUSIVE: value })).toThrow(/exclusive/i);
    },
  );

  it("accepts the default preview controller on port 18090", () => {
    expect(t2PreviewTargets(env)).toEqual(targets);
  });

  it("accepts an explicit preview controller with a trailing slash", () => {
    expect(t2PreviewTargets({ ...env, E2E_STACK_CONTROLLER_URL: "http://127.0.0.1:18090/" })).toEqual(targets);
  });

  it("accepts empty bundle and contract variables", () => {
    expect(t2PreviewTargets({ ...env, E2E_STACK_BUNDLE: "", E2E_CONTRACT: "" })).toEqual(targets);
  });

  it.each(["localhost", "[::1]"])("accepts loopback host %s", (host) => {
    expect(t2PreviewTargets({ ...env, E2E_STACK_URL: `http://${host}:18000` })).toEqual({
      gateway: `http://${host}:18000`, controller: `http://${host}:18090`,
    });
  });

  it.each([undefined, ""])("requires E2E_STACK_URL (got %j)", (value) => {
    expect(() => t2PreviewTargets({ ...env, E2E_STACK_URL: value })).toThrow(/T2 needs E2E_STACK_URL/);
  });

  it("refuses the live Gateway port 8000", () => {
    expect(() => t2PreviewTargets({ ...env, E2E_STACK_URL: "http://127.0.0.1:8000" })).toThrow(/live stack port/);
  });

  it.each(ports.live)("refuses live controller port %i", (port) => {
    expect(() => t2PreviewTargets({ ...env, E2E_STACK_CONTROLLER_URL: `http://127.0.0.1:${port}` })).toThrow(/live stack port/);
  });

  it.each([18190, 18191, 18000, 13000, 26686, 19090, 28090, 80, 65535])("refuses controller port %i instead of 18090", (port) => {
    expect(() => t2PreviewTargets({ ...env, E2E_STACK_URL: "http://localhost:18000", E2E_STACK_CONTROLLER_URL: `http://127.0.0.1:${port}` })).toThrow(/preview CONTROLLER origin on port 18090/);
  });

  it("refuses a controller without an explicit port 18090", () => {
    expect(() => t2PreviewTargets({ ...env, E2E_STACK_CONTROLLER_URL: "http://127.0.0.1" })).toThrow(/preview CONTROLLER origin on port 18090/);
  });

  it("refuses port 18090 when absent from PREVIEW_STACK_PORTS", () => {
    const has = vi.spyOn(PREVIEW_STACK_PORTS, "has").mockReturnValue(false);
    try {
      expect(() => t2PreviewTargets(env)).toThrow(/preview CONTROLLER origin on port 18090/);
      expect(has).toHaveBeenCalledWith("18090");
    } finally {
      has.mockRestore();
    }
  });

  it.each([
    ["https Gateway", { ...env, E2E_STACK_URL: "https://127.0.0.1:18000" }, /http:/],
    ["https controller", { ...env, E2E_STACK_CONTROLLER_URL: "https://127.0.0.1:18090" }, /http:/],
    ["a non-loopback Gateway IP", { ...env, E2E_STACK_URL: "http://192.168.1.5:18000" }, /loopback/],
    ["a non-loopback Gateway hostname", { ...env, E2E_STACK_URL: "http://example.com:18000" }, /loopback/],
    ["a non-loopback controller IP", { ...env, E2E_STACK_CONTROLLER_URL: "http://192.168.1.5:18090" }, /loopback/],
    ["a non-loopback controller hostname", { ...env, E2E_STACK_CONTROLLER_URL: "http://example.com:18090" }, /loopback/],
  ])("refuses %s", (_name, value, message) => {
    expect(() => t2PreviewTargets(value)).toThrow(message);
  });

  it.each([
    ["user info", "http://user:pw@127.0.0.1:18090"],
    ["a path", "http://127.0.0.1:18090/x"],
    ["a query", "http://127.0.0.1:18090?a=1"],
    ["a hash", "http://127.0.0.1:18090#x"],
    ["a normalized path", "http://127.0.0.1:18090/x/.."],
    ["an empty query", "http://127.0.0.1:18090?"],
    ["an empty hash", "http://127.0.0.1:18090#"],
    ["empty user info", "http://@127.0.0.1:18090"],
  ])("refuses %s on E2E_STACK_CONTROLLER_URL", (_name, url) => {
    expect(() => t2PreviewTargets({ ...env, E2E_STACK_CONTROLLER_URL: url })).toThrow(/origin only/);
  });

  it("refuses the controller on the Gateway's origin even on port 18090", () => {
    expect(() => t2PreviewTargets({ ...env, E2E_STACK_URL: targets.controller, E2E_STACK_CONTROLLER_URL: targets.controller })).toThrow(/same origin/);
  });

  it("refuses E2E_STACK_BUNDLE=local", () => {
    expect(() => t2PreviewTargets({ ...env, E2E_STACK_BUNDLE: "local" })).toThrow(/image-served operator.*unset E2E_STACK_BUNDLE/);
  });

  it("refuses E2E_CONTRACT=1", () => {
    expect(() => t2PreviewTargets({ ...env, E2E_CONTRACT: "1" })).toThrow(/unset E2E_CONTRACT/);
  });
});

describe("browser origin allowlist", () => {
  const allowed = ["http://127.0.0.1:18000"];

  it("lets a request through only on an allowed origin", () => {
    expect(allowedOrigin("http://127.0.0.1:18000/api/products", allowed)).toBe(true);
    for (const url of ["http://127.0.0.1:18090/faults/status", "http://127.0.0.1:8000/health", "http://localhost:18000/", "https://127.0.0.1:18000/", "data:text/plain,x"]) {
      expect(allowedOrigin(url, allowed), url).toBe(false);
    }
  });

  it("operator tests allow the controller's origin instead", () => {
    expect(allowedOrigin("http://127.0.0.1:18090/faults/status", ["http://127.0.0.1:18090"])).toBe(true);
    expect(allowedOrigin("http://127.0.0.1:18000/api/products", ["http://127.0.0.1:18090"])).toBe(false);
  });
});
