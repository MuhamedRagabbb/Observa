// @vitest-environment node
// P5-B: what the manual fault section shows and allows (src/operator/manual/manual-view.ts). The
// controller's `controls.activate` is the only authority on whether a fault can start; the page
// adds no rule of its own, and while the page cannot reach the controller nothing can be started.
import { describe, expect, it } from "vitest";
import { controllerStatus } from "../e2e/fixtures/controller.ts";
import { defaultForm } from "../src/operator/manual/fault-form.ts";
import { manualView } from "../src/operator/manual/manual-view.ts";

const draft = defaultForm("bad_deployment");
const view = (status: ReturnType<typeof controllerStatus> | null, connection: "connecting" | "connected" | "disconnected" = "connected") => manualView({ connection, status, draft });

describe("idle", () => {
  it("is open: the operator's draft is shown and Inject fault is allowed", () => {
    expect(view(controllerStatus("idle"))).toEqual({ locked: false, lockNotice: null, selected: "bad_deployment", activeScenario: null, activation: null, form: draft });
  });
});

describe("a manual fault is running", () => {
  it("locks the section, shows the running fault's own settings and marks it Active", () => {
    const result = view(controllerStatus("manual"));
    expect(result).toMatchObject({
      locked: true,
      lockNotice: "One manual fault at a time. Stop the active fault to change parameters.",
      selected: "payments_errors",
      activeScenario: "payments_errors",
      form: { scenario: "payments_errors", settings: { failure_rate: "90", status_code: "503" }, duration: "300" },
    });
  });

  it("a fault this page does not know keeps the draft and still locks", () => {
    const status = controllerStatus("manual", { active: { ...controllerStatus("manual").active!, scenario: "future_fault" as never } });
    expect(view(status)).toMatchObject({ locked: true, selected: "bad_deployment", activeScenario: null, form: draft });
  });
});

describe("every other lock comes from the controller's own reason", () => {
  it.each([
    ["automatic", "Unavailable while an automatic cycle is running."],
    ["recovering", "Unavailable while services recover from the last fault."],
    ["unknown", "Unavailable until the fault status is confirmed."],
  ] as const)("%s", (state, notice) => {
    expect(view(controllerStatus(state))).toMatchObject({ locked: true, lockNotice: notice, form: expect.anything() });
  });

  it("a reason this page does not know still locks, with a general notice", () => {
    const status = controllerStatus("idle", { controls: { ...controllerStatus("idle").controls, activate: { enabled: false, reason: "maintenance_window" } } });
    expect(view(status)).toMatchObject({ locked: true, lockNotice: "Unavailable right now." });
  });

  it("while a stop is unconfirmed the draft is kept, never the unconfirmed fault's settings", () => {
    expect(view(controllerStatus("unknown"))).toMatchObject({ selected: "bad_deployment", form: draft });
  });
});

describe("no usable connection", () => {
  it("connecting: locked until the controller answers", () => {
    expect(view(null, "connecting")).toMatchObject({ locked: true, lockNotice: "Unavailable until the controller answers.", selected: "bad_deployment" });
  });

  it("disconnected: locked even though the last answer said idle", () => {
    expect(view(controllerStatus("idle"), "disconnected")).toMatchObject({ locked: true, lockNotice: "Unavailable while the controller is unreachable." });
  });

  it("disconnected with nothing ever read is locked too", () => {
    expect(view(null, "disconnected")).toMatchObject({ locked: true, lockNotice: "Unavailable while the controller is unreachable." });
  });
});

describe("a fault the controller has not acknowledged yet (review: never claim active before the acknowledgement)", () => {
  const starting = () => {
    const base = controllerStatus("manual");
    return controllerStatus("manual", { active: { ...base.active!, status: "activating", acknowledged_at: null } });
  };

  it("is shown as starting, never as Active", () => {
    expect(view(starting())).toMatchObject({ locked: true, activeScenario: "payments_errors", activation: "pending" });
  });

  it("an acknowledged fault is Active", () => {
    expect(view(controllerStatus("manual"))).toMatchObject({ activeScenario: "payments_errors", activation: "acknowledged" });
  });

  it("no fault: no activation state", () => {
    expect(view(controllerStatus("idle"))).toMatchObject({ activation: null });
  });
});

describe("a status that has not been re-read for a while (review: tab resumed with an old answer)", () => {
  it("locks the section until the controller has answered again", () => {
    expect(manualView({ connection: "connected", status: controllerStatus("idle"), draft, stale: true })).toMatchObject({
      locked: true,
      lockNotice: "Waiting for a fresh answer from the controller.",
    });
  });

  it("a fresh answer is not stale by default", () => {
    expect(manualView({ connection: "connected", status: controllerStatus("idle"), draft })).toMatchObject({ locked: false });
  });
});
