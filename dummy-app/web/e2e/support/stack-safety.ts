// Where stack-mode tests may go (kept free of Playwright so Vitest can check it; see
// test/stack-safety.test.ts). A preview project's Gateway and controller only: never the live
// stack's ports, only loopback http, and the controller never on the Gateway's origin.
import { PREVIEW_STACK_PORTS, refuseLive } from "../../preview-guard.ts";

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]"]);

function checked(name: string, url: string): string {
  const parsed = new URL(refuseLive(name, url));
  if (parsed.protocol !== "http:") throw new Error(`${name}=${url} must be an http: URL (a local preview project)`);
  if (!LOOPBACK.has(parsed.hostname)) throw new Error(`${name}=${url} must be a loopback address (a local preview project)`);
  return parsed.origin;
}

/** The preview project's Gateway and controller origins, or null when stack mode is off. */
export function stackTargets(env: Record<string, string | undefined>): { gateway: string; controller: string } | null {
  if (!env.E2E_STACK_URL) return null;
  const gateway = checked("E2E_STACK_URL", env.E2E_STACK_URL);
  const controller = checked("E2E_STACK_CONTROLLER_URL", env.E2E_STACK_CONTROLLER_URL ?? `http://${new URL(gateway).hostname}:18090`);
  if (controller === gateway) throw new Error("The controller and the Gateway cannot share the same origin");
  return { gateway, controller };
}

/** T2 verifies only the image-served operator on the preview controller. */
export function t2PreviewTargets(env: Record<string, string | undefined>): { gateway: string; controller: string } {
  const targets = stackTargets(env); // refuses live ports, non-loopback and non-http URLs
  if (!env.E2E_STACK_URL || !targets) throw new Error("T2 needs E2E_STACK_URL for an isolated preview project; NEVER use the live stack");
  if (env.E2E_T2_EXCLUSIVE !== "1") throw new Error("T2 requires exclusive preview access attested by E2E_T2_EXCLUSIVE=1");
  const controller = new URL(targets.controller);
  if (!PREVIEW_STACK_PORTS.has(controller.port) || controller.port !== "18090") {
    throw new Error("T2 requires a loopback http preview CONTROLLER origin on port 18090, distinct from the Gateway; live/harness/other preview ports are forbidden");
  }
  const value = env.E2E_STACK_CONTROLLER_URL ?? targets.controller;
  const configured = new URL(value);
  // Check the spelling too: URL normalizes /x/.., empty ?/# and empty user info.
  const parts = /^[^:]+:\/\/([^/?#]*)(.*)$/.exec(value);
  if (configured.username || configured.password || configured.pathname !== "/" || configured.search || configured.hash
      || !parts || /[?#@\\]/.test(value) || (parts[2] !== "" && parts[2] !== "/")) {
    throw new Error("T2 requires E2E_STACK_CONTROLLER_URL to be an origin only (no user info, path, query or hash)");
  }
  if (env.E2E_STACK_BUNDLE) throw new Error("T2 requires the P5-G image-served operator at /; unset E2E_STACK_BUNDLE");
  if (env.E2E_CONTRACT) throw new Error("T2 requires preview stack mode; unset E2E_CONTRACT");
  return targets;
}

/** Whether a browser request may go to `url`: only to one of the allowed origins. */
export function allowedOrigin(url: string, allowed: readonly string[]): boolean {
  try {
    return allowed.includes(new URL(url).origin);
  } catch {
    return false;
  }
}

function contractOrigin(name: string, value: string | undefined): string {
  if (!value) throw new Error(`${name} is required`);
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${name} is not a valid URL`);
  }
  if (url.protocol !== "http:") throw new Error(`${name} must be an http: URL`);
  if (url.hostname !== "127.0.0.1") throw new Error(`${name} must use host 127.0.0.1`);
  // Check the original spelling too: URL normalizes /x/.., empty ?/# and empty user info.
  const parts = /^[^:]+:\/\/([^/?#]*)(.*)$/.exec(value);
  if (!parts || /[?#@\\]/.test(value) || (parts[2] !== "" && parts[2] !== "/") || url.pathname !== "/" || url.username || url.password) {
    throw new Error(`${name} must be an origin only (no path, query or user info)`);
  }
  if (parts[1]?.split(":")[0] !== "127.0.0.1") throw new Error(`${name} must use host 127.0.0.1`);
  const port = /:([0-9]+)$/.exec(parts[1] ?? "")?.[1];
  if (!port) throw new Error(`${name} must name a port`);
  if (!/^[1-9][0-9]*$/.test(port)) throw new Error(`${name} port must be an ASCII decimal without leading zeroes`);
  if (Number(port) < 1024 || Number(port) > 65535) throw new Error(`${name} port must be 1024-65535`);
  refuseLive(name, value);
  if (PREVIEW_STACK_PORTS.has(url.port)) {
    throw new Error(`${name} is a preview project port; the contract harness never takes its place`);
  }
  return url.origin;
}

/** Explicit, isolated origins for the local harness; never defaults to a running service. */
export function contractTarget(env: Record<string, string | undefined>): { controller: string; control: string } | null {
  if (env.E2E_CONTRACT === undefined || env.E2E_CONTRACT === "") return null;
  if (env.E2E_CONTRACT !== "1") throw new Error("E2E_CONTRACT must be 1");
  for (const [name, value] of Object.entries(env)) {
    if (["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"].includes(name.toUpperCase()) && value) {
      throw new Error(`${name} proxy must be absent or empty for contract tests`);
    }
  }
  if (env.E2E_STACK_URL !== undefined && (!env.E2E_CONTRACT_CONTROLLER_URL || !env.E2E_CONTRACT_CONTROL_URL)) {
    throw new Error("Use one mode at a time: E2E_CONTRACT and E2E_STACK_URL cannot coexist");
  }
  const controller = contractOrigin("E2E_CONTRACT_CONTROLLER_URL", env.E2E_CONTRACT_CONTROLLER_URL);
  const control = contractOrigin("E2E_CONTRACT_CONTROL_URL", env.E2E_CONTRACT_CONTROL_URL);
  if (controller === control) throw new Error("The controller and control cannot share the same origin");
  if (env.E2E_STACK_URL !== undefined) throw new Error("Use one mode at a time: E2E_CONTRACT and E2E_STACK_URL cannot coexist");
  return { controller, control };
}
