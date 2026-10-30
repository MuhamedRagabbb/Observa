// @vitest-environment node
import { describe, expect, it } from "vitest";
import {
  MutationGate,
  OwnershipLedger,
  foreignActivity,
  healthMismatch,
  observedPhases,
  quietWindow,
  recoveryDeadline,
  recoveryVerdict,
  recoveringUiEvidence,
  type GateKind,
  type MutationRequest,
  type StatusSample,
} from "../e2e/support/t2-evidence.ts";

const endedAt = "2026-10-30T20:00:00.000Z";
const ended = Date.parse(endedAt) / 1000;
const incident = { incident_id: "INC-1", scenario: "PAYMENT" };
const cycleConfig = {
  seed: 7341, scenarios: ["payments_errors"], shuffle: false,
  warmup_seconds: 0, duration_seconds: 5, recovery_seconds: 5, repeat: 2,
};
const cycleIdentity = { config: cycleConfig, startedAt: ended };

function sample(overrides: Partial<StatusSample> = {}): StatusSample {
  return {
    state: "idle",
    active: null,
    auto_running: false,
    auto: null,
    last_fault: { ended_at: endedAt },
    now: ended + 2,
    ...overrides,
  };
}

function runningCycle(auto: Partial<NonNullable<StatusSample["auto"]>> = {}): StatusSample {
  return sample({
    state: "automatic", auto_running: true,
    auto: { phase: "fault", repeat: 1, step: 1, phase_started_at: ended + 1, config: cycleConfig, ...auto },
  });
}

function statuses(): StatusSample[] {
  return [
    sample(),
    sample({ state: "manual", active: incident }),
    sample({ state: "automatic", active: incident, auto_running: true, auto: { phase: "fault" } }),
    sample({ state: "automatic", auto_running: true, auto: { phase: "recovery" } }),
    sample({ state: "recovering" }),
    sample({ state: "unknown" }),
  ];
}

const stages = ["fresh", "unresolved-manual", "unresolved-cycle", "owned-manual", "owned-cycle"] as const;
type Stage = (typeof stages)[number];

function ledgerAt(stage: Stage): OwnershipLedger {
  const ledger = new OwnershipLedger();
  if (stage === "unresolved-manual" || stage === "owned-manual") ledger.begin("manual");
  if (stage === "unresolved-cycle" || stage === "owned-cycle") ledger.begin("cycle");
  if (stage === "owned-manual") ledger.confirmManual("INC-1");
  if (stage === "owned-cycle") ledger.confirmCycle(cycleIdentity);
  return ledger;
}

describe("OwnershipLedger", () => {
  it("owns nothing initially, even when a pre-existing fault or cycle is visible", () => {
    const ledger = new OwnershipLedger();
    expect(ledger.unresolved).toBe(false);
    for (const status of statuses()) {
      expect(ledger.plan(status)).toEqual({ actions: [], report: null });
    }
  });

  it.each(stages)("stops nothing and reports unreadable status for a %s ledger", (stage) => {
    const plan = ledgerAt(stage).plan(null);
    expect(plan.actions).toEqual([]);
    expect(plan.report).toMatch(/unreadable/i);
    expect(plan.report).toMatch(/nothing (?:was )?stopped/i);
  });

  it.each(["manual", "cycle"] as const)("never infers ownership after an unconfirmed %s start", (kind) => {
    const ledger = new OwnershipLedger();
    ledger.begin(kind);
    expect(ledger.unresolved).toBe(true);
    // The manual sample deliberately has the same PAYMENT scenario as our intended start.
    for (const status of statuses()) {
      const plan = ledger.plan(status);
      expect(plan.actions).toEqual([]);
      expect(plan.report).toMatch(/OWNERSHIP/i);
      expect(plan.report).toMatch(/unproven/i);
      expect(ledger.unresolved).toBe(true);
    }
  });

  it("deactivates exactly the confirmed manual incident and resolves the start", () => {
    const ledger = ledgerAt("owned-manual");
    expect(ledger.unresolved).toBe(false);
    expect(ledger.plan(sample({ state: "manual", active: incident }))).toEqual({
      actions: ["deactivate-manual"], report: null,
    });
  });

  it("does not deactivate a different incident even when its scenario matches", () => {
    expect(ledgerAt("owned-manual").plan(sample({
      state: "manual", active: { ...incident, incident_id: "INC-OTHER" },
    }))).toEqual({ actions: [], report: null });
  });

  it("does nothing when the confirmed manual incident is no longer active", () => {
    const ledger = ledgerAt("owned-manual");
    for (const state of ["idle", "manual", "recovering", "unknown"]) {
      expect(ledger.plan(sample({ state, active: null }))).toEqual({ actions: [], report: null });
    }
  });

  it("never lets a manual owner stop a running cycle, even with the same incident id", () => {
    const ledger = ledgerAt("owned-manual");
    for (const state of ["manual", "automatic"]) {
      expect(ledger.plan(sample({ state, active: incident, auto_running: true }))).toEqual({
        actions: [], report: null,
      });
    }
  });

  it("stops an owned running cycle without planning a manual deactivation", () => {
    const ledger = ledgerAt("owned-cycle");
    expect(ledger.unresolved).toBe(false);
    for (const active of [incident, null]) {
      expect(ledger.plan({ ...runningCycle(), active })).toEqual({
        actions: ["stop-cycle"], report: null,
      });
    }
  });

  it.each(["idle", "automatic"])("plan fails closed for a confirmed running cycle with null auto in %s state", (state) => {
    const ledger = ledgerAt("owned-cycle");
    const status = sample({ state, auto_running: true, auto: null });
    const check = ledger.stopCheck(status);
    expectDenied(check);
    expect(ledger.plan(status)).toEqual({ actions: [], report: check.reason });
  });

  it.each([
    ["different config echo", { config: { ...cycleConfig, seed: 99 } }],
    ["decreased repeat", { repeat: 1 }],
    ["decreased step", { step: 1 }],
    ["decreased phase_started_at", { phase_started_at: ended + 9 }],
  ] as const)("plan fails closed for a confirmed cycle with %s", (_name, change) => {
    const ledger = ledgerAt("owned-cycle");
    const progress = { repeat: 2, step: 2, phase_started_at: ended + 10 };
    ledger.observe(runningCycle(progress));
    const status = runningCycle({ ...progress, ...change });
    const check = ledger.stopCheck(status);
    expectDenied(check);
    expect(ledger.plan(status)).toEqual({ actions: [], report: check.reason });
  });

  it.each([0, 1])("plan stops a confirmed cycle with equal config and progress advanced by %s", (advance) => {
    const ledger = ledgerAt("owned-cycle");
    ledger.observe(runningCycle());
    const status = runningCycle({
      config: structuredClone(cycleConfig), repeat: 1 + advance, step: 1 + advance,
      phase_started_at: ended + 1 + advance,
    });
    expect(ledger.plan(status)).toEqual({ actions: ["stop-cycle"], report: null });
  });

  it("does nothing once an owned cycle stops, including when a manual fault is now active", () => {
    const ledger = ledgerAt("owned-cycle");
    for (const status of statuses()) {
      expect(ledger.plan({ ...status, auto_running: false })).toEqual({ actions: [], report: null });
    }
  });

  it.each(stages)("finish clears all ownership and unresolved state from %s", (stage) => {
    const ledger = ledgerAt(stage);
    ledger.finish();
    expect(ledger.unresolved).toBe(false);
    for (const status of statuses()) {
      expect(ledger.plan(status)).toEqual({ actions: [], report: null });
    }
  });

  it("rejects cycle confirmation after beginning a manual start", () => {
    const ledger = ledgerAt("unresolved-manual");
    expect(() => ledger.confirmCycle(cycleIdentity)).toThrow();
  });

  it("rejects manual confirmation after beginning a cycle start", () => {
    const ledger = ledgerAt("unresolved-cycle");
    expect(() => ledger.confirmManual("INC-1")).toThrow();
  });

  it.each(stages.filter((stage) => stage !== "fresh"))(
    "rejects either kind of second begin while %s",
    (stage) => {
      for (const kind of ["manual", "cycle"] as const) {
        const ledger = ledgerAt(stage);
        expect(() => ledger.begin(kind)).toThrow();
      }
    },
  );

  it("allows a new owned start after finish", () => {
    const ledger = ledgerAt("owned-manual");
    ledger.finish();
    ledger.begin("cycle");
    expect(ledger.unresolved).toBe(true);
    ledger.confirmCycle(cycleIdentity);
    expect(ledger.unresolved).toBe(false);
    expect(ledger.plan(runningCycle())).toEqual({
      actions: ["stop-cycle"], report: null,
    });
  });
});

function expectDenied(check: { allowed: boolean; reason: string | null }) {
  expect(check.allowed).toBe(false);
  expect(check.reason).toEqual(expect.any(String));
  expect(check.reason?.length).toBeGreaterThan(0);
}

function expectRetired(ledger: OwnershipLedger) {
  expect(ledger.unresolved).toBe(false);
  expect(ledger.revoked).toBeNull();
  for (const status of [...statuses(), runningCycle()]) {
    expect(ledger.plan(status)).toEqual({ actions: [], report: null });
    expectDenied(ledger.stopCheck(status));
  }
}

function expectRevoked(ledger: OwnershipLedger) {
  expect(ledger.revoked).toEqual(expect.any(String));
  expect(ledger.revoked?.length).toBeGreaterThan(0);
  expect(ledger.unresolved).toBe(false);
  for (const status of [null, ...statuses(), runningCycle()]) {
    const plan = ledger.plan(status);
    expect(plan.actions).toEqual([]);
    expect(plan.report).toMatch(/OWNERSHIP/);
    expect(plan.report).toMatch(/revoked/i);
    expect(plan.report).toContain(ledger.revoked);
    expectDenied(ledger.stopCheck(status));
  }
}

describe("OwnershipLedger observation and revocation", () => {
  it("observe revokes a manual owner when a cycle appears with the same incident id", () => {
    const ledger = ledgerAt("owned-manual");
    ledger.observe({ ...runningCycle(), active: incident });
    expectRevoked(ledger);
  });

  it("observe keeps manual ownership and allows Stop for the same incident without a cycle", () => {
    const ledger = ledgerAt("owned-manual");
    const status = sample({ state: "manual", active: incident, auto_running: false });
    ledger.observe(status);
    expect(ledger.revoked).toBeNull();
    expect(ledger.plan(status)).toEqual({ actions: ["deactivate-manual"], report: null });
    expect(ledger.stopCheck(status)).toEqual({ allowed: true, reason: null });
  });

  it("observe revokes a cycle whose phase_started_at predates its confirmed identity", () => {
    const ledger = ledgerAt("owned-cycle");
    const status = runningCycle({ phase_started_at: cycleIdentity.startedAt - 1 });
    ledger.observe(status);
    expectRevoked(ledger);
    expectDenied(ledger.stopCheck(status));
  });

  it.each([0, 1])("observe keeps a cycle with phase_started_at at identity startedAt plus %s", (offset) => {
    const ledger = ledgerAt("owned-cycle");
    const status = runningCycle({ phase_started_at: cycleIdentity.startedAt + offset });
    ledger.observe(status);
    expect(ledger.revoked).toBeNull();
    expect(ledger.plan(status)).toEqual({ actions: ["stop-cycle"], report: null });
    expect(ledger.stopCheck(status)).toEqual({ allowed: true, reason: null });
  });

  it("keeps a manual owner when observe sees the same incident id", () => {
    const ledger = ledgerAt("owned-manual");
    const status = sample({ state: "manual", active: incident });
    ledger.observe(status);
    expect(ledger.revoked).toBeNull();
    expect(ledger.unresolved).toBe(false);
    expect(ledger.plan(status)).toEqual({ actions: ["deactivate-manual"], report: null });
  });

  it("retires a manual owner from observe alone when active becomes null, before later assertions", () => {
    const ledger = ledgerAt("owned-manual");
    ledger.observe(sample({ state: "recovering" }));
    // Deliberately no plan/finish/assertion between completion and replacement work.
    ledger.observe(sample({ state: "manual", active: { ...incident, incident_id: "INC-OTHER" } }));
    expectRetired(ledger);
  });

  it("revokes when observe sees a different manual incident id even with the same scenario", () => {
    const ledger = ledgerAt("owned-manual");
    ledger.observe(sample({ state: "manual", active: { ...incident, incident_id: "INC-OTHER" } }));
    ledger.observe(sample({ state: "manual", active: incident }));
    expectRevoked(ledger);
  });

  it.each(["idle", "recovering", "manual"])(
    "retires a cycle from observe alone when auto_running becomes false in %s",
    (state) => {
      const ledger = ledgerAt("owned-cycle");
      ledger.observe(sample({ state, active: state === "manual" ? incident : null }));
      // A foreign cycle arriving before an assertion must never become ours.
      ledger.observe(runningCycle());
      expectRetired(ledger);
    },
  );

  it("keeps cycle ownership for deep-equal config, equal progress, and increasing progress", () => {
    const ledger = ledgerAt("owned-cycle");
    const config = {
      repeat: 2, recovery_seconds: 5, duration_seconds: 5, warmup_seconds: 0,
      shuffle: false, scenarios: ["payments_errors"], seed: 7341,
    };
    const initial = runningCycle({ config });
    ledger.observe(initial);
    ledger.observe(initial);
    const next = runningCycle({ config, repeat: 2, step: 2, phase_started_at: ended + 10 });
    ledger.observe(next);
    expect(ledger.revoked).toBeNull();
    expect(ledger.plan(next)).toEqual({ actions: ["stop-cycle"], report: null });
    expect(ledger.stopCheck(next)).toEqual({ allowed: true, reason: null });
  });

  it("compares the first observed cycle config to the confirmed controller echo", () => {
    const ledger = new OwnershipLedger();
    ledger.begin("cycle");
    ledger.confirmCycle({ config: { ...cycleConfig, seed: 99 }, startedAt: ended });
    ledger.observe(runningCycle());
    expectRevoked(ledger);
  });

  it("revokes a running cycle whose auto object is missing", () => {
    const ledger = ledgerAt("owned-cycle");
    ledger.observe(sample({ state: "automatic", auto_running: true, auto: null }));
    expectRevoked(ledger);
  });

  it.each([
    ["config", { config: { ...cycleConfig, scenarios: ["bad_deployment"] } }],
    ["repeat", { repeat: 1 }],
    ["step", { step: 1 }],
    ["phase_started_at", { phase_started_at: ended + 9 }],
    ["a restart at warmup/step 0", { phase: "warmup", repeat: 1, step: 0, phase_started_at: ended + 20 }],
  ] as const)("revokes on changed/decreased %s and cannot be revived by later observations", (_name, change) => {
    const ledger = ledgerAt("owned-cycle");
    const progress = { repeat: 2, step: 2, phase_started_at: ended + 10 };
    ledger.observe(runningCycle());
    ledger.observe(runningCycle(progress));
    ledger.observe(runningCycle({ ...progress, ...change }));
    ledger.observe(runningCycle({ repeat: 2, step: 3, phase_started_at: ended + 30 }));
    expectRevoked(ledger);
  });

  it.each(["fresh", "unresolved-manual", "unresolved-cycle"] as const)(
    "observe is a no-op for a %s ledger",
    (stage) => {
      const ledger = ledgerAt(stage);
      for (const status of statuses()) ledger.observe(status);
      expect(ledger.revoked).toBeNull();
      expect(ledger.unresolved).toBe(stage !== "fresh");
      for (const status of statuses()) {
        expect(ledger.plan(status)).toEqual(ledgerAt(stage).plan(status));
      }
    },
  );

  it.each(["owned-manual", "owned-cycle"] as const)("explicitly revokes %s with a retained reason", (stage) => {
    const ledger = ledgerAt(stage);
    ledger.revoke("foreign controller_started after reservation");
    ledger.observe(sample());
    ledger.observe(runningCycle());
    expect(ledger.revoked).toBe("foreign controller_started after reservation");
    expectRevoked(ledger);
  });

  it.each(["fresh", "unresolved-manual", "unresolved-cycle"] as const)(
    "revoke does not authorize work or change unresolved state for %s",
    (stage) => {
      const ledger = ledgerAt(stage);
      ledger.revoke("foreign activity");
      expect(ledger.unresolved).toBe(stage !== "fresh");
      if (stage === "fresh") expect(ledger.revoked).toBeNull();
      expect(ledger.plan(runningCycle()).actions).toEqual([]);
      expectDenied(ledger.stopCheck(runningCycle()));
    },
  );

  it.each(["owned-manual", "owned-cycle"] as const)("revoke on a retired %s ledger records nothing", (stage) => {
    const ledger = ledgerAt(stage);
    ledger.observe(sample());
    ledger.revoke("activity after our completion");
    expectRetired(ledger);
  });
});

describe("OwnershipLedger stopCheck", () => {
  it.each(["fresh", "owned-manual", "owned-cycle"] as const)("denies apparently owned statuses in the not owned branch after %s", (stage) => {
    const ledger = ledgerAt(stage);
    if (stage !== "fresh") ledger.finish();
    // Pin the "not owned" branch with otherwise valid manual and cycle statuses.
    for (const status of [
      sample({ state: "manual", active: incident }),
      sample({ state: "manual", active: { ...incident, incident_id: "INC-OTHER" } }),
      runningCycle(),
    ]) expectDenied(ledger.stopCheck(status));
  });

  it.each(stages)("denies unreadable status for %s", (stage) => {
    const check = ledgerAt(stage).stopCheck(null);
    expectDenied(check);
    expect(check.reason).toMatch(/unreadable/i);
  });

  it.each(["fresh", "unresolved-manual", "unresolved-cycle"] as const)("denies every readable status for %s", (stage) => {
    const ledger = ledgerAt(stage);
    for (const status of [...statuses(), runningCycle()]) expectDenied(ledger.stopCheck(status));
  });

  it("allows a manual Stop only for the owned id without a running cycle", () => {
    expect(ledgerAt("owned-manual").stopCheck(sample({ state: "manual", active: incident }))).toEqual({
      allowed: true, reason: null,
    });
  });

  it.each([
    ["no active incident", sample()],
    ["a different incident id", sample({ state: "manual", active: { ...incident, incident_id: "INC-OTHER" } })],
    ["a running cycle even with the owned id", sample({ state: "automatic", active: incident, auto_running: true })],
  ] as const)("denies manual Stop with %s", (_name, status) => {
    expectDenied(ledgerAt("owned-manual").stopCheck(status));
  });

  it("allows a confirmed running automatic cycle with deep-equal config before any observe call", () => {
    expect(ledgerAt("owned-cycle").stopCheck(runningCycle({ config: structuredClone(cycleConfig) }))).toEqual({
      allowed: true, reason: null,
    });
  });

  it.each([
    ["auto_running false", { ...runningCycle(), auto_running: false }],
    ["state other than automatic", { ...runningCycle(), state: "unknown" }],
    ["missing auto", { ...runningCycle(), auto: null }],
    ["missing config", runningCycle({ config: undefined })],
    ["different config", runningCycle({ config: { ...cycleConfig, repeat: 3 } })],
  ] as const)("denies cycle Stop with %s", (_name, status) => {
    expectDenied(ledgerAt("owned-cycle").stopCheck(status));
  });

  it.each([
    ["repeat", { repeat: 1 }],
    ["step", { step: 1 }],
    ["phase_started_at", { phase_started_at: ended + 9 }],
  ] as const)("denies cycle Stop when %s decreases against the last observed progress", (_name, change) => {
    const ledger = ledgerAt("owned-cycle");
    const progress = { repeat: 2, step: 2, phase_started_at: ended + 10 };
    ledger.observe(runningCycle());
    ledger.observe(runningCycle(progress));
    expectDenied(ledger.stopCheck(runningCycle({ ...progress, ...change })));
  });

  it("a fresh GET is not an atomic guard: consecutive stopCheck calls answer only their supplied status", () => {
    const ledger = ledgerAt("owned-manual");
    expect(ledger.stopCheck(sample({ state: "manual", active: incident }))).toEqual({ allowed: true, reason: null });
    // Another writer can act between this GET and a later plan/request. The controller has
    // no conditional operations; exclusive preview access remains a required precondition.
    expectDenied(ledger.stopCheck(sample({ state: "manual", active: { ...incident, incident_id: "INC-OTHER" } })));
  });
});

describe("recoveryDeadline", () => {
  it("adds the default 180 seconds to the controller's ISO fault end timestamp", () => {
    expect(recoveryDeadline({ ended_at: endedAt })).toBe(ended + 180);
  });

  it("honours an explicit recovery limit, including zero", () => {
    expect(recoveryDeadline({ ended_at: endedAt }, 45)).toBe(ended + 45);
    expect(recoveryDeadline({ ended_at: endedAt }, 0)).toBe(ended);
  });
});

describe("recoveryVerdict", () => {
  it("honours a non-default two-second skew tolerance without changing the 180 second limit", () => {
    const options = { toleranceSeconds: 2 };
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 181 })], options)).toEqual({
      verdict: "pending", elapsedSeconds: 181,
    });
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 182 })], options)).toEqual({
      verdict: "overran", elapsedSeconds: 182,
    });
  });
  it("is pending with no elapsed time when there are no samples", () => {
    expect(recoveryVerdict([])).toEqual({ verdict: "pending", elapsedSeconds: null });
  });

  it("is pending when the latest sample has no last fault, even if an earlier one did", () => {
    expect(recoveryVerdict([sample({ last_fault: null })])).toEqual({ verdict: "pending", elapsedSeconds: null });
    expect(recoveryVerdict([sample(), sample({ last_fault: null })])).toEqual({ verdict: "pending", elapsedSeconds: null });
  });

  it("accepts a first observation already idle two server seconds after fault completion", () => {
    expect(recoveryVerdict([sample()])).toEqual({ verdict: "idle", elapsedSeconds: 2 });
  });

  it("accepts fallback idle at 180.5 seconds after recovering at 179 seconds", () => {
    expect(recoveryVerdict([
      sample({ state: "recovering", now: ended + 120 }),
      sample({ state: "recovering", now: ended + 179 }),
      sample({ now: ended + 180.5 }),
    ])).toEqual({ verdict: "idle", elapsedSeconds: 180.5 });
  });

  it("does not infer an overrun merely from a late observation of idle", () => {
    expect(recoveryVerdict([
      sample({ state: "recovering", now: ended + 179 }),
      sample({ now: ended + 240 }),
    ])).toEqual({ verdict: "idle", elapsedSeconds: 240 });
  });

  it("retains an observed recovery overrun even after a later idle sample", () => {
    const overrun = sample({ state: "recovering", now: ended + 185 });
    expect(recoveryVerdict([overrun])).toEqual({ verdict: "overran", elapsedSeconds: 185 });
    expect(recoveryVerdict([overrun, sample({ now: ended + 186 })]).verdict).toBe("overran");
  });

  it("is pending at 184.9 seconds, inside the five-second clock/journal/polling skew tolerance", () => {
    const now = ended + 184.9;
    expect(recoveryVerdict([sample({ state: "recovering", now })])).toEqual({
      verdict: "pending", elapsedSeconds: now - ended,
    });
  });

  it("overruns exactly at the inclusive 185 second boundary (180 second limit plus at most five seconds of skew)", () => {
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 185 })])).toEqual({
      verdict: "overran", elapsedSeconds: 185,
    });
  });

  it("is pending while the latest recovery sample remains before the boundary", () => {
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 179 })])).toEqual({
      verdict: "pending", elapsedSeconds: 179,
    });
  });

  it.each(["manual", "automatic"])("does not call a newly started %s fault idle", (state) => {
    expect(recoveryVerdict([
      sample(),
      sample({ state, active: incident, auto_running: state === "automatic", now: ended + 300 }),
    ]).verdict).toBe("pending");
  });

  it("honours a custom limit independently of the default tolerance", () => {
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 35 })], {
      limitSeconds: 30,
    })).toEqual({ verdict: "overran", elapsedSeconds: 35 });
  });

  it("honours custom tolerance on both sides of its inclusive boundary", () => {
    const options = { limitSeconds: 30, toleranceSeconds: 5 };
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 34 })], options)).toEqual({
      verdict: "pending", elapsedSeconds: 34,
    });
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 35 })], options)).toEqual({
      verdict: "overran", elapsedSeconds: 35,
    });
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 181 })], {
      toleranceSeconds: 5,
    })).toEqual({ verdict: "pending", elapsedSeconds: 181 });
  });

  it("honours an explicit zero tolerance", () => {
    expect(recoveryVerdict([sample({ state: "recovering", now: ended + 180 })], {
      toleranceSeconds: 0,
    })).toEqual({ verdict: "overran", elapsedSeconds: 180 });
  });

  it("uses each sample's server clock and fault timestamp, regardless of calendar date", () => {
    for (const timestamp of ["2001-01-01T00:00:00.000Z", "2099-01-01T00:00:00.000Z"]) {
      const faultEnd = Date.parse(timestamp) / 1000;
      expect(recoveryVerdict([sample({ last_fault: { ended_at: timestamp }, now: faultEnd + 2 })])).toEqual({
        verdict: "idle", elapsedSeconds: 2,
      });
    }
  });

  it("evaluates only the supplied array without retaining evidence from later calls", () => {
    const prefix: readonly StatusSample[] = [sample({ state: "recovering", now: ended + 179 })];
    const extended = [...prefix, sample({ state: "recovering", now: ended + 185 })];
    const idle: readonly StatusSample[] = [sample()];
    const pending = { verdict: "pending", elapsedSeconds: 179 };
    const recovered = { verdict: "idle", elapsedSeconds: 2 };
    expect(recoveryVerdict(prefix)).toEqual(pending);
    expect(recoveryVerdict(idle)).toEqual(recovered);
    expect(recoveryVerdict(extended).verdict).toBe("overran");
    expect(recoveryVerdict(idle)).toEqual(recovered);
    expect(recoveryVerdict(prefix)).toEqual(pending);
    expect(recoveryVerdict([])).toEqual({ verdict: "pending", elapsedSeconds: null });
    expect(recoveryVerdict(extended).verdict).toBe("overran");
  });
});

describe("observedPhases", () => {
  it("returns an empty sequence for no observations", () => {
    expect(observedPhases([])).toEqual([]);
  });

  it("deduplicates consecutive labels while retaining automatic phase transitions and later idle", () => {
    expect(observedPhases([
      sample(), sample(),
      sample({ state: "automatic", auto: { phase: "fault" } }),
      sample({ state: "automatic", auto: { phase: "fault" } }),
      sample({ state: "automatic", auto: { phase: "recovery" } }),
      sample({ state: "recovering" }), sample(),
    ])).toEqual(["idle", "automatic:fault", "automatic:recovery", "recovering", "idle"]);
  });

  it("accepts a direct automatic recovery to idle without inserting recovering", () => {
    expect(observedPhases([
      sample({ state: "automatic", auto: { phase: "recovery" } }), sample(),
    ])).toEqual(["automatic:recovery", "idle"]);
  });

  it("uses plain automatic when phase is absent", () => {
    expect(observedPhases([
      sample({ state: "automatic", auto: null }),
      sample({ state: "automatic", auto: {} }),
    ])).toEqual(["automatic"]);
  });

  it("uses only state for non-automatic samples even if stale auto data remains", () => {
    expect(observedPhases([
      sample({ state: "manual", auto: { phase: "fault" } }),
      sample({ state: "manual", auto: { phase: "recovery" } }),
      sample({ state: "unknown" }),
      sample({ state: "manual" }),
    ])).toEqual(["manual", "unknown", "manual"]);
  });
});

const mutations: { kind: GateKind; path: string; body: unknown }[] = [
  { kind: "start-manual", path: "/faults/activate", body: {
    scenario: "payments_errors", duration_seconds: 30, parameters: { failure_rate: 0.5, status_code: 503 },
  } },
  // Match the UI wire payloads: manual Stop sends only scenario; cycle Stop has no body.
  { kind: "stop-manual", path: "/faults/deactivate", body: { scenario: "payments_errors" } },
  { kind: "start-cycle", path: "/faults/auto/start", body: cycleConfig },
  { kind: "stop-cycle", path: "/faults/auto/stop", body: undefined },
];

// Local extension keeps phase A typecheckable against the unchanged legacy gate.
// No runtime adapter: arm() still returns undefined and decide() still ignores provenance.
type ProvenanceRequest = MutationRequest & { token: string | null; redirected?: boolean };
function mutation(path: string, body: unknown, id = "request-1", token: unknown = null): ProvenanceRequest {
  return { id, method: "POST", path, body, token: token as string | null };
}

function expectRefused(gate: MutationGate, request: MutationRequest) {
  const before = gate.violations.length;
  const decision = gate.decide(request);
  expect(decision.allow).toBe(false);
  expect(decision.reason).toEqual(expect.any(String));
  expect(decision.reason?.length).toBeGreaterThan(0);
  expect(gate.violations).toHaveLength(before + 1);
  expect(gate.violations[before]).toEqual(expect.any(String));
  expect(gate.violations[before]?.length).toBeGreaterThan(0);
  expect(gate.isAuthorized(request.id)).toBe(false);
  return decision;
}

describe("MutationGate", () => {
  it.each(mutations)("refuses and records an unsolicited $kind", ({ path, body }) => {
    const gate = new MutationGate();
    expect(expectRefused(gate, mutation(path, body)).reason).toMatch(/unsolicited/i);
    expect(gate.violations[0]).toMatch(/unsolicited/i);
  });

  it.each(mutations)("allows exactly the armed $kind and authorizes only that request id", ({ kind, path, body }) => {
    const gate = new MutationGate();
    expect(gate.violations).toEqual([]);
    const token = gate.arm(kind, structuredClone(body));
    expect(gate.decide(mutation(path, body, "request-1", token))).toEqual({ allow: true, reason: null });
    expect(gate.isAuthorized("request-1")).toBe(true);
    expect(gate.isAuthorized("another-id-for-the-same-path")).toBe(false);
    expect(gate.isAuthorized("unknown")).toBe(false);
    expect(gate.violations).toEqual([]);
  });

  it.each(mutations)("refuses a wrong mutation path while preserving the armed $kind slot", ({ kind, path, body }) => {
    const gate = new MutationGate();
    const token = gate.arm(kind, body);
    const wrongPath = path === "/faults/activate" ? "/faults/deactivate" : "/faults/activate";
    expectRefused(gate, mutation(wrongPath, body, "foreign", token));
    expect(gate.decide(mutation(path, body, "request-1", token))).toEqual({ allow: true, reason: null });
    expect(gate.violations).toHaveLength(1);
  });

  it.each([
    ["failure_rate", 0, { scenario: "payments_errors", duration_seconds: 30, parameters: { failure_rate: 0.9, status_code: 503 } }],
    ["status_code", 0, { scenario: "payments_errors", duration_seconds: 30, parameters: { failure_rate: 0.5, status_code: 500 } }],
    ["manual Stop scenario", 1, { scenario: "bad_deployment" }],
    ["cycle scenarios", 2, { ...cycleConfig, scenarios: ["bad_deployment"] }],
    ["cycle repeat", 2, { ...cycleConfig, repeat: 3 }],
    ["cycle Stop object instead of no body", 3, {}],
    ["cycle Stop null instead of no body", 3, null],
  ] as const)("refuses a different %s body, records it, and leaves the slot armed", (_name, index, wrongBody) => {
    const { kind, path, body } = mutations[index]!;
    const gate = new MutationGate();
    const token = gate.arm(kind, body);
    expectRefused(gate, mutation(path, wrongBody, "wrong-body", token));
    expect(gate.decide(mutation(path, body, "request-1", token))).toEqual({ allow: true, reason: null });
    expect(gate.violations).toHaveLength(1);
  });

  it("deep-compares bodies without depending on nested object key order", () => {
    const gate = new MutationGate();
    const token = gate.arm("start-manual", mutations[0]!.body);
    expect(gate.decide(mutation("/faults/activate", {
      parameters: { status_code: 503, failure_rate: 0.5 }, duration_seconds: 30, scenario: "payments_errors",
    }, "request-1", token))).toEqual({ allow: true, reason: null });
  });

  it("retains one violation for each refused request while the authorized slot remains usable", () => {
    const gate = new MutationGate();
    const token = gate.arm("stop-manual", { scenario: "payments_errors" });
    expectRefused(gate, mutation("/faults/auto/stop", undefined, "wrong-path", token));
    expectRefused(gate, mutation("/faults/deactivate", { scenario: "bad_deployment" }, "wrong-scenario", token));
    expect(gate.violations).toHaveLength(2);
    expect(gate.decide(mutation("/faults/deactivate", { scenario: "payments_errors" }, "request-1", token))).toEqual({ allow: true, reason: null });
    expect(gate.violations).toHaveLength(2);
  });

  it.each(mutations)("consumes $kind once and refuses/records the next identical payload as a duplicate", ({ kind, path, body }) => {
    const gate = new MutationGate();
    const token = gate.arm(kind, body);
    expect(gate.decide(mutation(path, body, "request-1", token))).toEqual({ allow: true, reason: null });
    const decision = expectRefused(gate, mutation(path, body, "duplicate", token));
    expect(decision.reason).toMatch(/duplicate/i);
    expect(gate.violations[0]).toMatch(/duplicate/i);
    expect(gate.isAuthorized("request-1")).toBe(true);
  });

  it("throws when arm would replace an unconsumed slot, preserving the original authorization", () => {
    const gate = new MutationGate();
    const token = gate.arm("stop-manual", { scenario: "payments_errors" });
    expect(() => gate.arm("stop-cycle", undefined)).toThrow();
    expect(gate.decide(mutation("/faults/deactivate", { scenario: "payments_errors" }, "request-1", token))).toEqual({ allow: true, reason: null });
  });

  it("also rejects arming the same kind and body twice without consumption", () => {
    const gate = new MutationGate();
    gate.arm("stop-cycle", undefined);
    expect(() => gate.arm("stop-cycle", undefined)).toThrow();
  });

  it("can arm again after consumption and retains both authorized ids", () => {
    const gate = new MutationGate();
    const startToken = gate.arm("start-cycle", cycleConfig);
    expect(gate.decide(mutation("/faults/auto/start", cycleConfig, "start", startToken))).toEqual({ allow: true, reason: null });
    const stopToken = gate.arm("stop-cycle", undefined);
    expect(gate.decide(mutation("/faults/auto/stop", undefined, "stop", stopToken))).toEqual({ allow: true, reason: null });
    expect(gate.isAuthorized("start")).toBe(true);
    expect(gate.isAuthorized("stop")).toBe(true);
    expect(gate.violations).toEqual([]);
  });

  it.each([
    ["GET", "/faults/activate"], ["GET", "/faults/deactivate"],
    ["GET", "/faults/auto/start"], ["GET", "/faults/auto/stop"],
    ["HEAD", "/faults/status"], ["GET", "/activity"], ["HEAD", "/services/health"],
  ])("does not gate non-mutation %s %s or consume an armed slot", (method, path) => {
    const gate = new MutationGate();
    const request: ProvenanceRequest = { id: "read-or-other", method, path, body: undefined, token: null };
    // GETs are never gated; passing through is not a mutation authorization.
    expect(gate.decide(request)).toEqual({ allow: true, reason: null });
    expect(gate.isAuthorized(request.id)).toBe(false);
    const token = gate.arm("stop-cycle", undefined);
    expect(gate.decide(request)).toEqual({ allow: true, reason: null });
    expect(gate.decide(mutation("/faults/auto/stop", undefined, "request-1", token))).toEqual({ allow: true, reason: null });
    expect(gate.violations).toEqual([]);
  });

  it.each(["manual", "cycle"] as const)("an authorized %s start never implicitly arms its Stop", (kind) => {
    const start = mutations[kind === "manual" ? 0 : 2]!;
    const stop = mutations[kind === "manual" ? 1 : 3]!;
    const gate = new MutationGate();
    const token = gate.arm(start.kind, start.body);
    expect(gate.decide(mutation(start.path, start.body, "start", token))).toEqual({ allow: true, reason: null });
    expectRefused(gate, mutation(stop.path, stop.body, "unarmed-stop"));
    expect(gate.isAuthorized("start")).toBe(true);
  });

  it.each(mutations.flatMap((entry) => [null, "wrong-token"].map((token) => ({ ...entry, token }))))(
    "refuses $kind with token=$token without consuming the slot", ({ kind, path, body, token: badToken }) => {
      const gate = new MutationGate();
      const token = gate.arm(kind, body);
      expectRefused(gate, mutation(path, body, "unsolicited-before-click", badToken));
      expect(() => gate.arm(kind, body)).toThrow();
      expect(gate.decide(mutation(path, body, "intended", token))).toEqual({ allow: true, reason: null });
      expect(gate.isAuthorized("intended")).toBe(true);
      expect(gate.violations).toHaveLength(1);
    },
  );

  it.each(mutations)("refuses redirected $kind even with the armed token and preserves the slot", ({ kind, path, body }) => {
    const gate = new MutationGate();
    const token = gate.arm(kind, body);
    const redirected: ProvenanceRequest = { ...mutation(path, body, "redirected", token), redirected: true };
    const direct: ProvenanceRequest = { ...mutation(path, body, "direct", token), redirected: false };
    expectRefused(gate, redirected);
    expect(gate.decide(direct)).toEqual({ allow: true, reason: null });
    expect(gate.violations).toHaveLength(1);
  });

  it.each(mutations)("refuses an unarmed $kind even with a supplied token", ({ path, body }) => {
    expectRefused(new MutationGate(), mutation(path, body, "unarmed", "invented-token"));
  });

  it("returns fresh nonempty distinct tokens on successive arms, including the same kind and body", () => {
    const gate = new MutationGate();
    const first = gate.arm("stop-cycle", undefined);
    expect(first).toEqual(expect.any(String));
    expect(first).not.toBe("");
    expect(gate.decide(mutation("/faults/auto/stop", undefined, "first", first)).allow).toBe(true);
    const second = gate.arm("stop-cycle", undefined);
    expect(second).toEqual(expect.any(String));
    expect(second).not.toBe("");
    expect(second).not.toBe(first);
    expectRefused(gate, mutation("/faults/auto/stop", undefined, "stale-token", first));
    expect(gate.decide(mutation("/faults/auto/stop", undefined, "second", second)).allow).toBe(true);
  });

  it.each(mutations.flatMap((entry) => [
    `${entry.path}/`, `${entry.path}/other`, entry.path.toUpperCase(), `${entry.path}?alias=1`,
  ].map((alias) => ({ ...entry, alias }))))(
    "denies POST alias $alias before and during an armed canonical $kind", ({ kind, path, body, alias }) => {
      const gate = new MutationGate();
      expectRefused(gate, mutation(alias, body, "unarmed-alias", "invented"));
      const token = gate.arm(kind, body);
      expectRefused(gate, mutation(alias, body, "armed-alias", token));
      expect(gate.decide(mutation(path, body, "canonical", token))).toEqual({ allow: true, reason: null });
      expect(gate.violations).toHaveLength(2);
    },
  );

  it.each(["/services/health", "/activity", "/faults/status", "/unexpected"])(
    "denies every other controller POST to %s without consuming the slot", (path) => {
      const gate = new MutationGate();
      expectRefused(gate, mutation(path, {}, "unarmed-other"));
      const token = gate.arm("stop-cycle", undefined);
      expectRefused(gate, mutation(path, {}, "armed-other", token));
      expect(gate.decide(mutation("/faults/auto/stop", undefined, "stop", token)).allow).toBe(true);
      expect(gate.violations).toHaveLength(2);
    },
  );

  it.each(["PUT", "DELETE", "PATCH", "OPTIONS", "TRACE", "UNEXPECTED"].flatMap((method) =>
    [...mutations.map((entry) => entry.path), "/faults/status"].map((path) => ({ method, path })),
  ))("denies controller $method $path with or without an armed slot", ({ method, path }) => {
    const gate = new MutationGate();
    expectRefused(gate, { ...mutation(path, undefined, "unarmed-method"), method });
    const token = gate.arm("stop-cycle", undefined);
    expectRefused(gate, { ...mutation(path, undefined, "armed-method", token), method });
    expect(gate.decide(mutation("/faults/auto/stop", undefined, "stop", token)).allow).toBe(true);
    expect(gate.violations).toHaveLength(2);
  });

  it.each(["manual", "cycle"] as const)("node-side %s cleanup uses ledger plan, stopCheck and the returned token", (kind) => {
    const ledger = ledgerAt(kind === "manual" ? "owned-manual" : "owned-cycle");
    const status = kind === "manual" ? sample({ state: "manual", active: incident }) : runningCycle();
    const action = kind === "manual" ? "deactivate-manual" : "stop-cycle";
    expect(ledger.plan(status)).toEqual({ actions: [action], report: null });
    expect(ledger.stopCheck(status)).toEqual({ allowed: true, reason: null });
    const stop = mutations[kind === "manual" ? 1 : 3]!;
    const gate = new MutationGate();
    const token = gate.arm(stop.kind, stop.body);
    expect(gate.decide(mutation(stop.path, stop.body, "node-cleanup", token))).toEqual({ allow: true, reason: null });
    expect(gate.isAuthorized("node-cleanup")).toBe(true);
    expectRefused(gate, mutation(stop.path, stop.body, "node-duplicate", token));
  });

  it.each(["manual", "cycle"].flatMap((kind) => [null, "wrong-node-token"].map((token) => ({ kind, token })) ))(
    "refuses node-side $kind cleanup token=$token despite valid ownership", ({ kind, token: badToken }) => {
      const ledger = ledgerAt(kind === "manual" ? "owned-manual" : "owned-cycle");
      const status = kind === "manual" ? sample({ state: "manual", active: incident }) : runningCycle();
      expect(ledger.plan(status).actions).toEqual([kind === "manual" ? "deactivate-manual" : "stop-cycle"]);
      expect(ledger.stopCheck(status)).toEqual({ allowed: true, reason: null });
      const stop = mutations[kind === "manual" ? 1 : 3]!;
      const gate = new MutationGate();
      const token = gate.arm(stop.kind, stop.body);
      expectRefused(gate, mutation(stop.path, stop.body, "node-unproven", badToken));
      expect(gate.decide(mutation(stop.path, stop.body, "node-cleanup", token))).toEqual({ allow: true, reason: null });
      expect(gate.violations).toHaveLength(1);
    },
  );
});

const hostStart = 20_000;
function recoveringSamples(spanMs: number): StatusSample[] {
  return [
    sample({ state: "recovering", hostAt: hostStart }),
    sample({ state: "recovering", hostAt: hostStart + spanMs }),
  ];
}
function recoveringBanner(at: number) {
  return { at, kind: "recovering", title: "Waiting for services" };
}

describe("recoveringUiEvidence", () => {
  it("does not require recovering UI for no samples or a direct automatic recovery to Idle", () => {
    expect(recoveringUiEvidence([], [])).toEqual({ verdict: "not-required" });
    expect(recoveringUiEvidence([
      runningCycle({ phase: "recovery" }), sample({ hostAt: hostStart }),
    ], [])).toEqual({ verdict: "not-required" });
  });

  it.each([
    ["before the first sample at the inclusive lower bound", hostStart - 10_000],
    ["during recovery", hostStart + 5_000],
    ["after the last sample at the inclusive upper bound", hostStart + 22_000],
  ] as const)("keeps matching banner evidence %s even after a later Idle observation", (_name, at) => {
    expect(recoveringUiEvidence([
      ...recoveringSamples(12_000), sample({ hostAt: hostStart + 50_000 }),
    ], [recoveringBanner(at)])).toEqual({ verdict: "satisfied" });
  });

  it("accepts an unobservable recovering interval shorter than the default ten-second polling gap", () => {
    expect(recoveringUiEvidence(recoveringSamples(9_999), [])).toEqual({ verdict: "unobservable" });
  });

  it("treats a single timed recovering sample without a banner as unobservable", () => {
    expect(recoveringUiEvidence([sample({ state: "recovering", hostAt: hostStart })], [])).toEqual({ verdict: "unobservable" });
  });

  it("reports a missed banner for recovering longer than the polling gap", () => {
    expect(recoveringUiEvidence(recoveringSamples(10_001), [])).toEqual({ verdict: "missed" });
  });

  it.each([
    ["wrong title", { ...recoveringBanner(hostStart), title: "Recovery" }],
    ["wrong kind", { ...recoveringBanner(hostStart), kind: "automatic" }],
    ["one millisecond before the window", recoveringBanner(hostStart - 10_001)],
    ["one millisecond after the window", recoveringBanner(hostStart + 22_001)],
  ] as const)("does not count a snapshot with %s", (_name, snapshot) => {
    expect(recoveringUiEvidence(recoveringSamples(12_000), [snapshot])).toEqual({ verdict: "missed" });
  });

  it("uses host milliseconds rather than controller seconds to correlate banner evidence", () => {
    const samples = recoveringSamples(12_000).map((status) => ({ ...status, now: ended + 900 }));
    expect(recoveringUiEvidence(samples, [recoveringBanner(hostStart)])).toEqual({ verdict: "satisfied" });
    expect(recoveringUiEvidence(samples, [recoveringBanner((ended + 900) * 1_000)])).toEqual({ verdict: "missed" });
  });

  it("ignores samples without hostAt when forming the recovering window", () => {
    const samples = [sample({ state: "recovering" }), ...recoveringSamples(12_000), sample({ state: "recovering" })];
    expect(recoveringUiEvidence(samples, [])).toEqual({ verdict: "missed" });
    expect(recoveringUiEvidence(samples, [recoveringBanner(hostStart)])).toEqual({ verdict: "satisfied" });
  });

  it("is unobservable if recovering was sampled but every recovering sample lacks hostAt", () => {
    expect(recoveringUiEvidence([
      sample({ hostAt: 0 }), sample({ state: "recovering" }), sample({ hostAt: 100_000 }),
    ], [recoveringBanner(hostStart)])).toEqual({ verdict: "unobservable" });
  });

  it("honours a custom polling gap for both duration and snapshot window", () => {
    const samples = recoveringSamples(3_000);
    expect(recoveringUiEvidence(samples, [])).toEqual({ verdict: "unobservable" });
    expect(recoveringUiEvidence(samples, [], { pollGapSeconds: 2 })).toEqual({ verdict: "missed" });
    expect(recoveringUiEvidence(samples, [recoveringBanner(hostStart - 2_000)], { pollGapSeconds: 2 })).toEqual({ verdict: "satisfied" });
    expect(recoveringUiEvidence(samples, [recoveringBanner(hostStart - 2_001)], { pollGapSeconds: 2 })).toEqual({ verdict: "missed" });
  });
});

const expectedServices = ["api-gateway", "inventory-service", "orders-service", "payments-service"];
function health() {
  return { services: expectedServices.map((service, index) => ({
    service, state: ["healthy", "degraded", "unavailable", "unknown"][index]!,
  })) };
}
function healthRows() {
  return expectedServices.map((service, index) => ({
    service, label: ["Healthy", "Degraded", "Unavailable", "Unknown"][index]!,
  }));
}

describe("healthMismatch", () => {
  it("accepts exactly the expected services once each in any order with capitalised snapshot states", () => {
    expect(healthMismatch({ services: health().services.reverse() }, healthRows().reverse(), expectedServices)).toBeNull();
  });

  it("reports a missing snapshot", () => {
    expect(healthMismatch(null, healthRows(), expectedServices)).toMatch(/snapshot/i);
  });

  it.each([
    ["missing", () => health().services.slice(1)],
    ["duplicated instead of another expected service", () => [health().services[0]!, ...health().services.slice(0, 3)]],
    ["extra", () => [...health().services, { service: "foreign-service", state: "healthy" }]],
    ["replaced by an extra service", () => [...health().services.slice(1), { service: "foreign-service", state: "healthy" }]],
  ] as const)("reports snapshot services differing from expected when %s", (_name, services) => {
    expect(healthMismatch({ services: services() }, healthRows(), expectedServices)).toMatch(/snapshot.*(?:services|expected)/i);
  });

  it("reports a missing table service", () => {
    expect(healthMismatch(health(), healthRows().slice(1), expectedServices)).toMatch(/missing|api-gateway/i);
  });

  it("reports a duplicated table service even when every expected service is present", () => {
    const rows = [...healthRows(), healthRows()[0]!];
    expect(healthMismatch(health(), rows, expectedServices)).toMatch(/duplicat/i);
  });

  it("reports an extra table service", () => {
    expect(healthMismatch(health(), [...healthRows(), { service: "foreign-service", label: "Healthy" }], expectedServices)).toMatch(/extra|foreign-service/i);
  });

  it.each(["healthy", "Degraded", "Healthy "])("reports the differing api-gateway label %j", (label) => {
    const rows = healthRows();
    rows[0] = { service: "api-gateway", label };
    const mismatch = healthMismatch(health(), rows, expectedServices);
    expect(mismatch).toMatch(/api-gateway/);
    expect(mismatch).toMatch(/label|Healthy/);
  });
});

function quietSamples(spanSeconds = 4): StatusSample[] {
  return [sample({ now: ended }), sample({ now: ended + spanSeconds })];
}
function expectNotQuiet(samples: readonly StatusSample[], heads: readonly string[], minSeconds?: number) {
  const result = quietWindow(samples, heads, minSeconds);
  expect(result.quiet).toBe(false);
  expect(result.reason).toEqual(expect.any(String));
  expect(result.reason?.length).toBeGreaterThan(0);
}

describe("quietWindow", () => {
  it("accepts idle samples at the inclusive default four-controller-second boundary with stable activity", () => {
    expect(quietWindow(quietSamples(), ["head", "head"])).toEqual({ quiet: true, reason: null });
  });

  it("accepts a longer quiet window with at least one activity head", () => {
    expect(quietWindow(quietSamples(5), ["head"])).toEqual({ quiet: true, reason: null });
  });

  it("accepts deep-equal last_fault objects and also all-null last_fault values", () => {
    expect(quietWindow(quietSamples().map((status) => ({ ...status, last_fault: { ended_at: endedAt } })), ["head"])).toEqual({ quiet: true, reason: null });
    expect(quietWindow(quietSamples().map((status) => ({ ...status, last_fault: null })), ["head"])).toEqual({ quiet: true, reason: null });
  });

  it.each([0, 1])("refuses only %i status samples", (count) => {
    expectNotQuiet(quietSamples().slice(0, count), ["head"]);
  });

  it("refuses a window shorter than four seconds even if host time spans much longer", () => {
    const samples = quietSamples(3.999).map((status, index) => ({ ...status, hostAt: index * 100_000 }));
    expectNotQuiet(samples, ["head"]);
  });

  it.each([
    ["state is not idle", { state: "recovering" }],
    ["active incident exists", { active: incident }],
    ["automatic work is running", { auto_running: true }],
    ["stale auto object exists", { auto: { phase: "warmup" } }],
  ] as const)("refuses when an intermediate sample's %s", (_name, change) => {
    expectNotQuiet([sample({ now: ended }), sample({ now: ended + 2, ...change }), sample({ now: ended + 4 })], ["head", "head"]);
  });

  it.each([
    ["different ended_at", { ended_at: "2026-10-30T20:00:01.000Z" }],
    ["null after a fault", null],
  ] as const)("refuses a last_fault change (%s), even if it changes back", (_name, last_fault) => {
    expectNotQuiet([sample({ now: ended }), sample({ now: ended + 2, last_fault }), sample({ now: ended + 4 })], ["head"]);
  });

  it("refuses last_fault changing from null to an object", () => {
    expectNotQuiet([sample({ now: ended, last_fault: null }), sample({ now: ended + 4 })], ["head"]);
  });

  it("refuses absent activity heads", () => {
    expectNotQuiet(quietSamples(), []);
  });

  it("refuses any changed activity head, even if the latest equals the first", () => {
    expectNotQuiet(quietSamples(), ["head", "foreign-event", "head"]);
  });

  it("honours a custom minimum duration including zero", () => {
    expect(quietWindow(quietSamples(2), ["head"], 2)).toEqual({ quiet: true, reason: null });
    expectNotQuiet(quietSamples(4), ["head"], 5);
    expect(quietWindow(quietSamples(0), ["head"], 0)).toEqual({ quiet: true, reason: null });
  });
});

describe("foreignActivity", () => {
  const since = "2026-10-30T20:00:00.000Z";
  const newer = "2026-10-30T20:00:00.001Z";
  // Verified against app.py log.record calls, including its dynamic cycle outcomes.
  const controllerEvents = [
    "activation_unconfirmed", "control_rejected", "controller_started", "cycle_started",
    "cycle_stop_requested", "cycle_finished", "cycle_stopped", "cycle_failed", "fault_requested",
    "fault_active", "fault_stopped", "phase_started", "service_health_changed", "services_recovered",
    "stop_requested", "stop_unconfirmed",
  ];

  it("returns no findings for no entries, no newer entries, or wholly allowed entries", () => {
    expect(foreignActivity([], since, [])).toEqual([]);
    expect(foreignActivity([{ at: "2026-10-30T19:59:59.999Z", event: "controller_started" }], since, [])).toEqual([]);
    expect(foreignActivity(controllerEvents.map((event) => ({ at: newer, event })), since, controllerEvents)).toEqual([]);
  });

  it("uses strict lexicographic at > since, excluding events exactly at the boundary", () => {
    expect(foreignActivity([{ at: since, event: "controller_started" }], since, [])).toEqual([]);
    const findings = foreignActivity([{ at: newer, event: "controller_started" }], since, []);
    expect(findings).toHaveLength(1);
    expect(findings[0]).toContain("controller_started");
  });

  it.each(controllerEvents)("reports newer %s when that exact event is not allowed", (event) => {
    const findings = foreignActivity([{ at: newer, event }], since, ["unrelated_allowed_event"]);
    expect(findings).toHaveLength(1);
    expect(findings[0]).toContain(event);
  });

  it("returns one text per refused entry without collapsing repeated events or including allowed ones", () => {
    const entries = [
      { at: newer, event: "fault_requested" },
      { at: newer, event: "service_health_changed" },
      { at: "2026-10-30T20:00:00.002Z", event: "fault_requested" },
      { at: newer, event: "future_unknown_event" },
    ];
    const findings = foreignActivity(entries, since, ["service_health_changed"]);
    expect(findings).toHaveLength(3);
    expect(findings.filter((text: string) => text.includes("fault_requested"))).toHaveLength(2);
    expect(findings.some((text: string) => text.includes("future_unknown_event"))).toBe(true);
    expect(findings.some((text: string) => text.includes("service_health_changed"))).toBe(false);
  });
});
