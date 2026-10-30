// Browser tests for the web bundles.
// - Mocked mode (default): the production builds served by vite preview, every API call
//   answered by typed mocks (e2e/support/harness.ts). Run with `npm run e2e`.
// - Stack mode: E2E_STACK_URL=http://127.0.0.1:18000 runs only *.stack.spec.ts against
//   a running throwaway preview project, never the live stack.
// - Contract mode: E2E_CONTRACT=1 runs only *.contract.spec.ts against the local harness.
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";
import { VIEWPORTS } from "./e2e/coverage.ts";
import { refuseLive } from "./preview-guard.ts";
import { PREVIEW_PORTS } from "./e2e/support/ports.ts";
import { contractLauncher, venvPython } from "./e2e/support/contract-server.ts";
import { TEST_MATCH, testMode } from "./e2e/support/modes.ts";
import { contractTarget } from "./e2e/support/stack-safety.ts";

const contract = contractTarget(process.env);
const mode = testMode(process.env);
const stackUrl = process.env.E2E_STACK_URL ? `${refuseLive("E2E_STACK_URL", process.env.E2E_STACK_URL)}/` : undefined;
const root = fileURLToPath(new URL("../", import.meta.url));
const chromium = devices["Desktop Chrome"];

const preview = (config: string, port: number) => ({
  command: `npx vite preview --config ${config} --port ${port} --strictPort --host 127.0.0.1`,
  url: `http://127.0.0.1:${port}/ui/next/`,
  reuseExistingServer: false,
  timeout: 60_000,
});

export default defineConfig({
  testDir: "e2e",
  outputDir: "e2e-output/test-results",
  fullyParallel: mode !== "contract",
  // More parallel browsers starve the two preview servers on a workstation and cause timeouts.
  // Stack mode runs one test at a time: a fault on the preview reaches every checkout in flight.
  workers: mode === "mocked" ? 2 : 1,
  // Stack tests wait for real responses, and a fault test for the controller to finish recovering.
  ...(stackUrl ? { timeout: 300_000, expect: { timeout: 15_000 } } : {}),
  ...(contract ? {
    globalTimeout: 20 * 60_000,
    timeout: 60_000,
    expect: { timeout: 10_000 },
    globalTeardown: fileURLToPath(new URL("./e2e/support/contract-global-teardown.ts", import.meta.url)),
  } : {}),
  forbidOnly: true,
  retries: 0,
  reporter: [["list"], ["html", { outputFolder: "e2e-output/report", open: "never" }]],
  use: { trace: "retain-on-failure", locale: "en-US", timezoneId: "UTC" },
  projects: Object.entries(VIEWPORTS).map(([name, viewport]) => ({
    name: mode === "mocked" ? name : `${mode}-${name}`,
    testMatch: TEST_MATCH[mode],
    use: { ...chromium, viewport, deviceScaleFactor: 1, ...(stackUrl ? { baseURL: stackUrl } : {}), ...(contract ? { baseURL: `${contract.controller}/` } : {}) },
  })),
  webServer: contract
    ? [contractLauncher({ root, python: venvPython(root), targets: contract, exists: existsSync })]
    : stackUrl
    ? undefined
    : [
        preview("vite.store.config.ts", PREVIEW_PORTS.store),
        preview("vite.operator.config.ts", PREVIEW_PORTS.operator),
        // The test-only component gallery (e2e/gallery), built into e2e-output/ and never shipped.
        preview("vite.gallery.config.ts", PREVIEW_PORTS.gallery),
      ],
});
