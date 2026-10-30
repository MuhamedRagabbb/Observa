import { readFileSync } from "node:fs";

// Shared with the Python harness; fs + import.meta.url also works in Vite's config loader.
const ports = JSON.parse(readFileSync(new URL("./contracts/stack-ports.json", import.meta.url), "utf8")) as {
  live: number[];
  preview: Record<string, number>;
};
const LIVE_PORTS = new Set(ports.live.map(String));
export const PREVIEW_STACK_PORTS = new Set(Object.values(ports.preview).map(String));

/** Returns `url` without a trailing slash, or throws if it points at a live stack port. */
export function refuseLive(name: string, url: string): string {
  if (LIVE_PORTS.has(new URL(url).port)) {
    throw new Error(`${name}=${url} is a live stack port; point it at a preview project instead`);
  }
  return url.replace(/\/$/, "");
}
