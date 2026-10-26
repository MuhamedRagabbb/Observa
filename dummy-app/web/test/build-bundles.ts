import { execSync } from "node:child_process";
import { join } from "node:path";

// Runs the real production build before the tests. NODE_ENV is set explicitly because
// Vitest sets it to "test", which would bundle React's development build.
export default function buildBundles() {
  execSync("npm run build", {
    cwd: join(import.meta.dirname, ".."),
    env: { ...process.env, NODE_ENV: "production" },
    stdio: ["ignore", "ignore", "inherit"],
  });
}
