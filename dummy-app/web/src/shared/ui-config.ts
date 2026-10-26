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
