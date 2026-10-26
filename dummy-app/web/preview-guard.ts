// The live stack publishes the Gateway on 8000 and the controller on 8090. Nothing in web/
// (dev proxy, browser tests) may target them: that would place real orders or inject real faults.
const LIVE_PORTS = new Set(["8000", "8090"]);

/** Returns `url` without a trailing slash, or throws if it points at a live stack port. */
export function refuseLive(name: string, url: string): string {
  if (LIVE_PORTS.has(new URL(url).port)) {
    throw new Error(`${name}=${url} is a live stack port; point it at a preview project instead`);
  }
  return url.replace(/\/$/, "");
}
