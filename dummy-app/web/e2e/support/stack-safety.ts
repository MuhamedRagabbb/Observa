// Where stack-mode tests may go (kept free of Playwright so Vitest can check it; see
// test/stack-safety.test.ts). A preview project's Gateway and controller only: never the live
// stack's ports, only loopback http, and the controller never on the Gateway's origin.
import { refuseLive } from "../../preview-guard.ts";

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

/** Whether a browser request may go to `url`: only to one of the allowed origins. */
export function allowedOrigin(url: string, allowed: readonly string[]): boolean {
  try {
    return allowed.includes(new URL(url).origin);
  } catch {
    return false;
  }
}
