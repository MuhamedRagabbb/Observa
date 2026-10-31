import { cooperativeShutdown, processExists } from "./contract-shutdown.ts";
import { contractTarget } from "./stack-safety.ts";

export default async function globalTeardown() {
  try {
    const targets = contractTarget(process.env);
    if (!targets) return;
    const { outcome, detail } = await cooperativeShutdown({
      ...targets,
      fetch,
      sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      now: () => performance.now(),
      isAlive: processExists,
    });
    console.log(`contract-harness cooperative shutdown: ${outcome}${detail ? `: ${detail}` : ""}`);
  } catch (error) {
    // Refusal must not prevent the web-server plugin from performing its bounded kill fallback.
    console.log(`contract-harness cooperative shutdown: refused: ${error instanceof Error ? error.message : String(error)}`);
  }
}
