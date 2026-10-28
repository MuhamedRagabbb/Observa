// @vitest-environment node
// P5-0: stack-mode tests (storefront and, from P5-F, the Operator Dashboard) reach a running
// preview project's Gateway and controller. These checks decide where they may go: never the live
// stack, only loopback http (the controller's origin guard refuses anything else anyway), and the
// controller never on the Gateway's origin. In the browser, each request must stay on an allowed
// origin: the Gateway's by default, the controller's for operator tests.
import { describe, expect, it } from "vitest";
import { allowedOrigin, stackTargets } from "../e2e/support/stack-safety.ts";

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
