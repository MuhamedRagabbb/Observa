import { join } from "node:path";
import type { PlaywrightTestConfig } from "@playwright/test";
import { CONTRACT_PORTS } from "./ports.ts";

/** Defaults belong only to an explicit contract run, never to config loading. */
export function contractRunEnvironment(env: Record<string, string | undefined>) {
  return {
    ...env,
    E2E_CONTRACT: "1",
    E2E_CONTRACT_CONTROLLER_URL: env.E2E_CONTRACT_CONTROLLER_URL ?? `http://127.0.0.1:${CONTRACT_PORTS.controller}`,
    E2E_CONTRACT_CONTROL_URL: env.E2E_CONTRACT_CONTROL_URL ?? `http://127.0.0.1:${CONTRACT_PORTS.control}`,
  };
}

export function venvPython(root: string, platform: string = process.platform): string {
  return join(root, ".venv", platform === "win32" ? "Scripts/python.exe" : "bin/python");
}

export function contractLauncher({ root, python, targets, exists }: {
  root: string;
  python: string;
  targets: { controller: string; control: string };
  exists: (path: string) => boolean;
}) {
  if (!exists(python)) throw new Error(`Python is missing at ${python}; create the repo's venv first`);
  const ready = `contract-harness ready controller=${targets.controller} control=${targets.control}`;
  const escaped = ready.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return {
    command: `"${python}" -m tests.harness.operator_contract`,
    cwd: root,
    // Match a complete line in piped output, including Windows CRLF, without probing a socket.
    wait: { stdout: new RegExp(`^${escaped}\\r?$`, "m") },
    reuseExistingServer: false,
    timeout: 40_000,
    env: {
      CONTRACT_CONTROLLER_PORT: new URL(targets.controller).port,
      CONTRACT_CONTROL_PORT: new URL(targets.control).port,
    },
    gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
    stdout: "pipe",
    stderr: "pipe",
  } satisfies NonNullable<PlaywrightTestConfig["webServer"]>;
}
