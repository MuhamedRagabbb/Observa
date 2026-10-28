import { APP_PATHS } from "./bundle-path.ts";

// GET /ui/config, served by both the Gateway and the controller (services/shared/ui.py).
// It is deliberately left out of OpenAPI, so this type is written by hand. The shared
// contract in contracts/ui-config.json is checked against the backend by pytest and
// against this parser by Vitest.

export const UI_PORT_NAMES = ["store", "control", "grafana", "jaeger", "prometheus"] as const;
export type UiPortName = (typeof UI_PORT_NAMES)[number];

export type UiConfig = {
  /** "store" on the Gateway, "control" on the controller. */
  mode: "store" | "control";
  /** Public host ports of each local service. */
  ports: Record<UiPortName, number>;
};

const isPort = (value: unknown): value is number =>
  typeof value === "number" && Number.isInteger(value) && value > 0 && value < 65536;

/** Validates an untyped /ui/config response. Throws if it does not match the contract. */
export function parseUiConfig(value: unknown): UiConfig {
  const fail = (why: string): never => {
    throw new Error(`Unexpected /ui/config response: ${why}`);
  };
  if (typeof value !== "object" || value === null) return fail("not an object");
  const { mode, ports } = value as { mode?: unknown; ports?: unknown };
  if (mode !== "store" && mode !== "control") return fail("unknown mode");
  if (typeof ports !== "object" || ports === null) return fail("missing ports");
  const parsed = {} as Record<UiPortName, number>;
  for (const name of UI_PORT_NAMES) {
    const port = (ports as Record<string, unknown>)[name];
    if (!isPort(port)) return fail(`invalid ${name} port`);
    parsed[name] = port;
  }
  return { mode, ports: parsed };
}

/** GET /ui/config from the page's own origin (absolute path, so it works under /ui/next/). */
export async function fetchUiConfig(): Promise<UiConfig> {
  const response = await fetch("/ui/config", { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Unexpected /ui/config response: HTTP ${response.status}`);
  return parseUiConfig(await response.json());
}

/**
 * The URL of the other app on the same host: the page's scheme and host name, the target
 * app's public port from /ui/config, and that app's path. Never a hard-coded host.
 */
export function crossAppUrl(
  config: UiConfig,
  target: "store" | "control",
  page: URL | Location = window.location,
  path: string = APP_PATHS[target],
): string {
  const url = new URL(page.href);
  url.port = String(config.ports[target]);
  url.pathname = path;
  url.search = "";
  url.hash = "";
  return url.href;
}

/** An observability tool on the page's host, using its configured public port. */
export function toolUrl(config: UiConfig, port: UiPortName, page: URL | Location = window.location, path = "/"): string {
  const url = new URL(page.href);
  url.port = String(config.ports[port]);
  url.pathname = path;
  url.search = "";
  url.hash = "";
  return url.href;
}
