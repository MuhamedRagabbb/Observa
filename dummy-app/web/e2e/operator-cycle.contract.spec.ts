import type { Page } from "@playwright/test";
import { expect, test, type ContractController, type StubControl } from "./support/contract.ts";
import { activityEvents, banner, cyclePanel, expandAll, isPhone, openOperator, phoneBar, startCycle, title } from "./support/contract-operator.ts";

const WAIT = { timeout: 10_000 };
const input = (page: Page, name: string) => cyclePanel(page).getByRole("textbox", { name, exact: true });
const stopCycle = (page: Page) => banner(page).getByRole("button", { name: "Stop cycle", exact: true });
// cycle-form.ts accepts typed fractions; the +/- buttons move by 10 s. These are
// the actual form minima: warmup 0 s, fault 0.1 s, recovery 0 s (not 10 s).
const minimumCycle = { seed: 7341, scenarios: ["payments_errors"], shuffle: false, warmup_seconds: 0, duration_seconds: 0.1, recovery_seconds: 0, repeat: 1 };

async function mark(controller: ContractController, control: StubControl) {
  return { since: (await activityEvents(controller))[0]?.at ?? "", calls: (await control.calls()).length, requests: (await control.requests()).length };
}

async function prepareCycle(page: Page, duration = minimumCycle.duration_seconds, recovery = 0) {
  await openOperator(page, "Idle");
  await expandAll(page);
  for (const [name, value] of [["Warmup", "0"], ["Fault duration", String(duration)], ["Recovery", String(recovery)], ["Repeats", "1"], ["Seed", "7341"]] as const) {
    await input(page, name).fill(value);
    await expect(input(page, name)).toHaveValue(value);
  }
  for (const [name, selected] of [["Database Exhaustion", false], ["Payment Errors", true], ["Bad Deployment", false]] as const) {
    const checkbox = cyclePanel(page).getByRole("checkbox", { name, exact: true });
    await checkbox.setChecked(selected);
    await expect(checkbox).toBeChecked({ checked: selected });
  }
  await expect(cyclePanel(page).getByRole("switch", { name: "Shuffle scenarios", exact: true })).toHaveAttribute("aria-checked", "false");
  return { ...minimumCycle, duration_seconds: duration, recovery_seconds: recovery };
}

async function idle(page: Page, controller: ContractController) {
  await expect.poll(async () => (await controller.status()).state, WAIT).toBe("idle");
  expect(await controller.status()).toMatchObject({ active: null, auto: null, auto_running: false });
  await expect(title(page)).toHaveText("Idle", WAIT);
}

test("row 9: an observable UI cycle starts once, shows fault then recovery at step 1 of 1, and finishes itself", async ({ page, control, controller }) => {
  test.setTimeout(60_000);
  const checkpoint = await mark(controller, control);
  const shown = await prepareCycle(page, 6, 6);
  await startCycle(page).click();
  await expect(cyclePanel(page)).toContainText("Controller confirmed the cycle is running.", WAIT);
  expect((await controller.status()).auto?.config).toEqual(shown);
  // Each phase lasts long enough to be observed by the page's 2.5 s status poll.
  await expect(title(page)).toHaveText("Payment Errors", WAIT);
  await expect(banner(page)).toContainText("Fault phase", WAIT);
  await expect(banner(page)).toContainText("Repeat 1 of 1", WAIT);
  expect((await controller.status()).auto).toMatchObject({ phase: "fault", step: 1, steps: 1 });
  await expect(title(page)).toHaveText("Recovery", WAIT);
  await expect(banner(page)).toContainText("Recovery after Payment Errors", WAIT);
  await expect(banner(page)).toContainText("Repeat 1 of 1", WAIT);
  expect((await controller.status()).auto).toMatchObject({ phase: "recovery", step: 1, steps: 1 });
  await expect.poll(async () => (await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "cycle_finished").length, WAIT).toBe(1);
  await idle(page, controller);
  const events = await activityEvents(controller, checkpoint.since);
  expect(events.filter((event) => event.event === "cycle_started")).toHaveLength(1);
  expect(events.filter((event) => event.event === "cycle_finished")).toHaveLength(1);
  expect(events.filter((event) => event.event === "cycle_stop_requested")).toHaveLength(0);
  expect(events.filter((event) => event.event === "phase_started").reverse().map(({ phase, step }) => ({ phase, step }))).toEqual([
    { phase: "warmup", step: 0 }, { phase: "fault", step: 1 }, { phase: "recovery", step: 1 },
  ]);
  expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST").map((request) => request.path)).toEqual(["/faults/auto/start"]);
  expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(["PUT", "DELETE"]);
});

for (const surface of ["banner", "phone bar"] as const) {
  test(`row 9: ${surface} Stop cycle sends one stop request and confirms Idle`, async ({ page, control, controller }) => {
    test.skip(surface === "phone bar" && !isPhone(test.info()), "The phone stop bar exists only below 48rem.");
    const checkpoint = await mark(controller, control);
    const shown = await prepareCycle(page, 30);
    await startCycle(page).click();
    await expect(cyclePanel(page)).toContainText("Controller confirmed the cycle is running.", WAIT);
    await expect(title(page)).toHaveText("Payment Errors", WAIT);
    expect((await controller.status()).auto?.config).toEqual(shown);
    const button = surface === "banner" ? stopCycle(page) : phoneBar(page).getByRole("button", { name: "Stop automatic cycle", exact: true });
    await button.click();
    await expect(page.getByText("Controller confirmed the cycle stopped.", { exact: true })).toBeVisible(WAIT);
    await idle(page, controller);
    const events = await activityEvents(controller, checkpoint.since);
    expect(events.filter((event) => event.event === "cycle_started")).toHaveLength(1);
    expect(events.filter((event) => event.event === "cycle_stop_requested")).toHaveLength(1);
    expect(events.filter((event) => event.event === "cycle_stopped")).toHaveLength(1);
    expect(events.filter((event) => event.event === "cycle_finished")).toHaveLength(0);
    expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST").map((request) => request.path)).toEqual([
      "/faults/auto/start", "/faults/auto/stop",
    ]);
    expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(["PUT", "DELETE"]);
  });
}

test("row 11: double click on Start cycle sends exactly one automatic start", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  const shown = await prepareCycle(page, 30);
  await startCycle(page).dblclick();
  await expect(cyclePanel(page)).toContainText("Controller confirmed the cycle is running.", WAIT);
  await expect(title(page)).toHaveText("Payment Errors", WAIT);
  expect((await controller.status()).auto?.config).toEqual(shown);
  expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "cycle_started")).toHaveLength(1);
  expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST" && request.path === "/faults/auto/start")).toHaveLength(1);
  expect((await control.calls()).slice(checkpoint.calls)).toMatchObject([{ target: "payments", method: "PUT" }]);
});
