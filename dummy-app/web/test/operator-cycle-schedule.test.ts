// @vitest-environment node
// P5-C: the planned schedule (src/operator/cycle/schedule.ts), from the controller's real cycle
// semantics (controllers/fault_injector/app.py run_auto): a warmup, then for each repeat every
// chosen scenario gets its fault for `duration` and then `recovery` seconds, the last one included.
import { describe, expect, it } from "vitest";
import { formatDuration, plannedSchedule, scheduleFromAutoConfig, scheduleProgress } from "../src/operator/cycle/schedule.ts";

const config = (overrides: Partial<Parameters<typeof plannedSchedule>[0]> = {}) => ({ warmup: 120, duration: 120, recovery: 90, repeat: 1, scenarioCount: 3, ...overrides });

describe("planned schedule", () => {
  it("is the warmup, then a fault and a recovery for every scenario in every repeat", () => {
    const plan = plannedSchedule(config({ warmup: 60, duration: 120, recovery: 180, repeat: 2, scenarioCount: 2 }));
    expect(plan.steps).toBe(4);
    expect(plan.totalSeconds).toBe(60 + 4 * (120 + 180));
    expect(plan.segments).toEqual([
      { kind: "warmup", seconds: 60 },
      ...Array.from({ length: 4 }, () => [{ kind: "fault", seconds: 120 }, { kind: "recovery", seconds: 180 }]).flat(),
    ]);
  });

  it("the defaults: a 120 s warmup and three scenarios of 120 s + 90 s make 12.5 min", () => {
    const plan = plannedSchedule(config());
    expect(plan.totalSeconds).toBe(120 + 3 * 210);
    expect(plan.segments).toHaveLength(7);
  });

  it("a zero warmup or zero recovery leaves that segment out, but the maths holds", () => {
    const plan = plannedSchedule(config({ warmup: 0, recovery: 0, scenarioCount: 1, repeat: 2 }));
    expect(plan.segments).toEqual([{ kind: "fault", seconds: 120 }, { kind: "fault", seconds: 120 }]);
    expect(plan.totalSeconds).toBe(240);
  });

  it("too many steps to draw: the total is still exact, the bar is not drawn", () => {
    const plan = plannedSchedule(config({ repeat: 21, scenarioCount: 3 })); // 63 steps
    expect(plan.segments).toBeNull();
    expect(plan.steps).toBe(63);
    expect(plan.totalSeconds).toBe(120 + 63 * 210);
    expect(plannedSchedule(config({ repeat: 20, scenarioCount: 3 })).segments).not.toBeNull(); // 60 steps is the largest bar
  });
});

describe("how long, in words", () => {
  it.each([
    [45, "45 s"],
    [0.5, "0.5 s"],
    [60, "1 min"],
    [90, "1 min 30 s"],
    [1080, "18 min"],
    [3599, "59 min 59 s"],
    [3600, "1 h"],
    [3900, "1 h 5 min"],
    [3930, "1 h 5 min 30 s"],
    [3601, "1 h 1 s"],
    [7325, "2 h 2 min 5 s"],
    [0, "0 s"],
  ])("%s s is %s", (seconds, text) => {
    expect(formatDuration(seconds)).toBe(text);
  });
});

describe("where the running cycle is", () => {
  const plan = plannedSchedule(config({ warmup: 60, duration: 120, recovery: 180, repeat: 2, scenarioCount: 2 })).segments!;
  // indexes: 0 warmup, then fault/recovery pairs: step 1 = 1,2; step 2 = 3,4; step 3 = 5,6; step 4 = 7,8

  it("warmup", () => {
    expect(scheduleProgress(plan, { phase: "warmup", step: 0 })).toEqual({ now: 0, done: 0 });
  });

  it("the fault of step 1 and its recovery", () => {
    expect(scheduleProgress(plan, { phase: "fault", step: 1 })).toEqual({ now: 1, done: 1 });
    expect(scheduleProgress(plan, { phase: "recovery", step: 1 })).toEqual({ now: 2, done: 2 });
  });

  it("a later step has everything before it done", () => {
    expect(scheduleProgress(plan, { phase: "fault", step: 4 })).toEqual({ now: 7, done: 7 });
    expect(scheduleProgress(plan, { phase: "recovery", step: 4 })).toEqual({ now: 8, done: 8 });
  });

  it("stopping, or nothing known: no 'now' and nothing claimed done", () => {
    expect(scheduleProgress(plan, { phase: "stopping", step: 2 })).toEqual({ now: null, done: 0 });
    expect(scheduleProgress(plan, null)).toEqual({ now: null, done: 0 });
  });

  it("a step beyond the plan is clamped, never crashes", () => {
    expect(scheduleProgress(plan, { phase: "fault", step: 99 })).toEqual({ now: null, done: 0 });
  });

  it("with no recovery segment the recovery phase has no 'now', the fault before it is done", () => {
    const short = plannedSchedule(config({ warmup: 0, recovery: 0, scenarioCount: 2, repeat: 1 })).segments!;
    expect(scheduleProgress(short, { phase: "fault", step: 1 })).toEqual({ now: 0, done: 0 });
    expect(scheduleProgress(short, { phase: "recovery", step: 1 })).toEqual({ now: null, done: 1 });
  });
});

describe("the schedule of a cycle the controller is running (its own numbers, never the rounded form text)", () => {
  it("uses the exact seconds", () => {
    const plan = scheduleFromAutoConfig({ seed: 1, shuffle: false, scenarios: ["payments_errors"], warmup_seconds: 0.004, duration_seconds: 0.104, recovery_seconds: 0.004, repeat: 1000 });
    expect(plan.totalSeconds).toBeCloseTo(0.004 + 1000 * 0.108, 6);
    expect(plan.segments).toBeNull();
  });

  it("applies the controller's defaults to anything missing and does not care about the seed", () => {
    const plan = scheduleFromAutoConfig({ seed: 2147483648 } as never);
    expect(plan.totalSeconds).toBe(120 + 3 * 210);
  });
});
