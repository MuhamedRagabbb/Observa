// @vitest-environment node
// P5-C: what the Automatic cycle panel shows and allows (src/operator/cycle/cycle-view.ts). The
// controller's `controls.auto_start` is the only authority on starting; a running cycle is shown
// read-only from the controller's own config; nothing is startable without a fresh answer.
import { describe, expect, it } from "vitest";
import { controllerStatus } from "../e2e/fixtures/controller.ts";
import { cycleView } from "../src/operator/cycle/cycle-view.ts";
import { defaultCycleForm } from "../src/operator/cycle/cycle-form.ts";

const draft = { ...defaultCycleForm(), repeat: "2", seed: "9" };
const view = (status: ReturnType<typeof controllerStatus> | null, connection: "connecting" | "connected" | "disconnected" = "connected", stale = false) => cycleView({ connection, status, draft, stale });

describe("idle", () => {
  it("is open and shows the operator's draft, with no progress", () => {
    expect(view(controllerStatus("idle"))).toEqual({ locked: false, lockNotice: null, running: false, progress: null, form: draft });
  });
});

describe("a cycle is running", () => {
  it("is locked, shows the controller's own config, and says where it is", () => {
    const result = view(controllerStatus("automatic"));
    expect(result).toMatchObject({
      locked: true,
      lockNotice: "Cycle running. Settings are locked until it stops.",
      running: true,
      progress: "Repeat 2 of 3 · fault phase",
      form: { warmup: "60", duration: "120", recovery: "180", repeat: "3", shuffle: true, seed: "7341", scenarios: ["db_connection_exhaustion", "payments_errors"] },
    });
  });

  it.each([
    ["warmup", "Repeat 2 of 3 · warmup phase"],
    ["recovery", "Repeat 2 of 3 · recovery phase"],
    ["stopping", "Repeat 2 of 3 · stopping"],
  ] as const)("%s", (phase, progress) => {
    const base = controllerStatus("automatic");
    expect(view(controllerStatus("automatic", { auto: { ...base.auto!, phase } })).progress).toBe(progress);
  });

  it("while the cycle is stopping and the controller gives no details, the draft stays and progress says stopping", () => {
    expect(view(controllerStatus("automatic", { auto: null }))).toMatchObject({ locked: true, running: true, progress: "Stopping the cycle", form: draft });
  });
});

describe("every other lock comes from the controller's own reason", () => {
  it.each([
    ["manual", "Unavailable while a manual fault is active."],
    ["recovering", "Unavailable while services recover from the last fault."],
    ["unknown", "Unavailable until the fault status is confirmed."],
  ] as const)("%s", (state, notice) => {
    expect(view(controllerStatus(state))).toMatchObject({ locked: true, lockNotice: notice, running: false, progress: null, form: draft });
  });

  it("a reason this page does not know still locks, with a general notice", () => {
    const status = controllerStatus("idle", { controls: { ...controllerStatus("idle").controls, auto_start: { enabled: false, reason: "maintenance_window" } } });
    expect(view(status)).toMatchObject({ locked: true, lockNotice: "Unavailable right now." });
  });

  it("a control that is missing or not exactly true never unlocks", () => {
    const controls = { ...controllerStatus("idle").controls } as Record<string, unknown>;
    delete controls.auto_start;
    expect(view(controllerStatus("idle", { controls } as never))).toMatchObject({ locked: true });
  });
});

describe("no usable connection", () => {
  it("connecting", () => {
    expect(view(null, "connecting")).toMatchObject({ locked: true, lockNotice: "Unavailable until the controller answers.", form: draft });
  });
  it("disconnected, even though the last answer said idle", () => {
    expect(view(controllerStatus("idle"), "disconnected")).toMatchObject({ locked: true, lockNotice: "Unavailable while the controller is unreachable.", running: false });
  });
  it("stale: waits for a fresh answer", () => {
    expect(view(controllerStatus("idle"), "connected", true)).toMatchObject({ locked: true, lockNotice: "Waiting for a fresh answer from the controller." });
  });
});
