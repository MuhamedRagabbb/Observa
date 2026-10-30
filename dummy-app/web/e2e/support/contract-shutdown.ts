import { contractTarget } from "./stack-safety.ts";

type ShutdownOptions = {
  control: string;
  controller: string;
  fetch: typeof fetch;
  sleep: (ms: number) => Promise<void>;
  now: () => number;
  /** Whether a process still exists (signal 0: sends nothing). */
  isAlive: (pid: number) => boolean;
  maxWaitMs?: number;
};

export type ShutdownResult = {
  outcome: "exited" | "timeout" | "unreachable" | "refused";
  detail?: string;
};

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** Signal 0 sends nothing; only ESRCH establishes that the process is gone. */
export function processExists(pid: number, kill = process.kill.bind(process)): boolean {
  try {
    kill(pid, 0);
    return true;
  } catch (error) {
    const code = (error as NodeJS.ErrnoException | null)?.code;
    if (code === "ESRCH") return false;
    if (code === "EPERM") return true;
    throw error;
  }
}

/** The process id the harness reports must name some other process: never this one or its parent. */
function plausiblePid(value: unknown): value is number {
  return typeof value === "number" && Number.isSafeInteger(value) && value > 1 && value < 2 ** 31
    && value !== process.pid && value !== process.ppid;
}

/**
 * Ask the harness to stop, then wait until ITS PROCESS is gone. A closed port proves nothing (uvicorn
 * closes its listeners before the controller's shutdown has finished), and the harness prints
 * "stopped cleanly" only after that shutdown, so the process ending by itself is the evidence.
 * Validates both origins before contacting anything and contacts only the control origin. Only runtime
 * failures are converted into results; origin validation still rejects (global teardown catches it).
 * Playwright's bounded force-kill remains the fallback.
 */
export async function cooperativeShutdown(options: ShutdownOptions): Promise<ShutdownResult> {
  const targets = contractTarget({
    E2E_CONTRACT: "1",
    E2E_CONTRACT_CONTROLLER_URL: options.controller,
    E2E_CONTRACT_CONTROL_URL: options.control,
  })!;
  const request = (path: string, method: string) =>
    options.fetch(`${targets.control}${path}`, {
      method,
      headers: { "x-contract-harness": "1" },
      redirect: "error",
      signal: AbortSignal.timeout(5_000),
    });

  let pid: number;
  try {
    const alive = await request("/__test__/alive", "GET");
    if (!alive.ok) return { outcome: "refused", detail: `alive: HTTP ${alive.status}` };
    const body = (await alive.json()) as { pid?: unknown };
    if (!plausiblePid(body.pid)) return { outcome: "refused", detail: "alive: implausible process id" };
    pid = body.pid;
  } catch (error) {
    return { outcome: "unreachable", detail: message(error) };
  }
  try {
    const stop = await request("/__test__/shutdown", "POST");
    if (!stop.ok) return { outcome: "refused", detail: `shutdown: HTTP ${stop.status}` };
  } catch (error) {
    return { outcome: "unreachable", detail: message(error) };
  }

  let detail: string | undefined;
  try {
    const deadline = options.now() + (options.maxWaitMs ?? 10_000);
    while (options.now() < deadline) {
      if (!options.isAlive(pid)) return { outcome: "exited" };
      const remaining = deadline - options.now();
      if (remaining > 0) await options.sleep(Math.min(250, remaining));
    }
  } catch (error) {
    detail = message(error);
  }
  return { outcome: "timeout", ...(detail === undefined ? {} : { detail }) };
}
