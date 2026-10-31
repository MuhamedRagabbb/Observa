export function testMode(env: Record<string, string | undefined>): "mocked" | "stack" | "contract" {
  if (env.E2E_CONTRACT !== undefined && env.E2E_CONTRACT !== "" && env.E2E_CONTRACT !== "1") {
    throw new Error("E2E_CONTRACT must be 1");
  }
  if (env.E2E_CONTRACT === "1") {
    if (env.E2E_STACK_URL !== undefined) throw new Error("Use one mode at a time: E2E_CONTRACT and E2E_STACK_URL cannot coexist");
    return "contract";
  }
  return env.E2E_STACK_URL ? "stack" : "mocked";
}

export const TEST_MATCH = {
  mocked: /.*(?<!\.stack|\.contract)\.spec\.ts$/,
  stack: /.*\.stack\.spec\.ts$/,
  contract: /.*\.contract\.spec\.ts$/,
} as const;
