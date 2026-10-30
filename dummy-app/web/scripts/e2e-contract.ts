// Run explicitly; importing the Playwright config never executes this launcher.
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { contractRunEnvironment } from "../e2e/support/contract-server.ts";

const require = createRequire(import.meta.url);
const child = spawn(process.execPath, [require.resolve("@playwright/test/cli"), "test", ...process.argv.slice(2)], {
  stdio: "inherit",
  env: contractRunEnvironment(process.env),
});
child.on("error", (error) => {
  console.error(`Could not start contract tests: ${error.message}`);
  process.exit(1);
});
child.on("exit", (code) => process.exit(code ?? 1));
