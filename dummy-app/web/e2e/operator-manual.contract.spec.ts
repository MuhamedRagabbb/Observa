import type { Page } from "@playwright/test";
import { expect, test, type ContractController, type StubControl } from "./support/contract.ts";
import { activityEvents, banner, expandAll, field, inject, isPhone, manual, openOperator, phoneBar, scenario, title } from "./support/contract-operator.ts";

const WAIT = { timeout: 10_000 };
/** The page ignores a stop-like press for 800 ms after its action ends (`cooldownUntil` in src/operator/manual/use-fault-actions.ts). */
const PAGE_STOP_COOLDOWN_MS = 800;
/** Added on top of the page's cooldown; the wait below starts at an OBSERVATION of the end of the action, which can only be late. */
const COOLDOWN_MARGIN_MS = 200;
const stop = (page: Page, name = "Stop fault") => banner(page).getByRole("button", { name, exact: true });
const payment = { scenario: "payments_errors", duration_seconds: 30, parameters: { failure_rate: 0.9, status_code: 503 } };

async function mark(controller: ContractController, control: StubControl) {
  return { since: (await activityEvents(controller))[0]?.at ?? "", calls: (await control.calls()).length, requests: (await control.requests()).length };
}

async function preparePayment(page: Page) {
  await openOperator(page, "Idle");
  await expandAll(page);
  await field(page, "Duration").fill("30");
}

async function startPayment(page: Page) {
  await preparePayment(page);
  await inject(page).click();
  await expect(manual(page)).toContainText("Controller confirmed the fault is active.", WAIT);
  await expect(title(page)).toHaveText("Payment Errors", WAIT);
}

async function idle(page: Page, controller: ContractController) {
  await expect.poll(async () => (await controller.status()).state, WAIT).toBe("idle");
  await expect(title(page)).toHaveText("Idle", WAIT);
}

async function browserPost(page: Page, path: string, body: unknown) {
  return page.evaluate(async ({ path, body }) => {
    const answer = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    return { status: answer.status, body: await answer.json() as Record<string, unknown> };
  }, { path, body });
}

const scenarios = [
  { code: "F1", id: "db_connection_exhaustion", name: "Database Exhaustion", target: "orders", field: "Connection hold", value: "4", parameters: { hold_seconds: 4 }, runtime: { db_hold_seconds: 4 } },
  { code: "F2", id: "payments_errors", name: "Payment Errors", target: "payments", field: "Failure rate", value: "40", parameters: { failure_rate: 0.4, status_code: 503 }, runtime: { payment_failure_rate: 0.4, payment_failure_status: 503 } },
  { code: "F3", id: "bad_deployment", name: "Bad Deployment", target: "orders", field: "Added delay", value: "1.25", parameters: { delay_seconds: 1.25 }, runtime: { release_version: "v2-buggy", persistence_delay_seconds: 1.25 } },
] as const;

for (const item of scenarios) {
  test(`row 3: ${item.code} starts once with the shown parameters and a decreasing countdown`, async ({ page, control, controller }) => {
    const checkpoint = await mark(controller, control);
    await preparePayment(page);
    await scenario(page, item.name).check();
    await field(page, item.field).fill(item.value);
    await expect(field(page, item.field)).toHaveValue(item.value);
    await expect(field(page, "Duration")).toHaveValue("30");
    await control.mode(item.target, "ack");
    await inject(page).click();
    await expect(manual(page)).toContainText("Controller confirmed the fault is active.", WAIT);
    await expect(title(page)).toHaveText(item.name, WAIT);
    const status = await controller.status();
    expect(status).toMatchObject({ state: "manual", active: { scenario: item.id, status: "active", parameters: item.parameters } });
    const clock = banner(page).getByText(/^\d{2}:\d{2}$/);
    await expect(clock).toBeVisible(WAIT);
    const seconds = (text: string) => text.split(":").reduce((total, part) => total * 60 + Number(part), 0);
    const earlier = seconds(await clock.innerText());
    await expect.poll(async () => seconds(await clock.innerText()), { timeout: 4_000 }).toBeLessThan(earlier);
    const events = await activityEvents(controller, checkpoint.since);
    expect(events.filter((event) => event.event === "fault_requested")).toHaveLength(1);
    expect(events.filter((event) => event.event === "fault_active")).toHaveLength(1);
    expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST" && request.path === "/faults/activate")).toHaveLength(1);
    const calls = (await control.calls()).slice(checkpoint.calls);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toEqual({ target: item.target, method: "PUT", at: expect.any(String), body: { ...item.runtime, expires_at: status.active?.expires_at } });
    await stop(page).click();
    await idle(page, controller);
  });
}

for (const surface of ["banner", "phone bar"] as const) {
  test(`row 4: ${surface} stop is confirmed once, then Recovering becomes Idle`, async ({ page, control, controller }) => {
    test.skip(surface === "phone bar" && !isPhone(test.info()), "The phone stop bar exists only below 48rem.");
    const checkpoint = await mark(controller, control);
    await startPayment(page);
    await control.setHealth({ "payments-service": "degraded" });
    const button = surface === "banner" ? stop(page) : phoneBar(page).getByRole("button", { name: "Stop Payment Errors", exact: true });
    await button.click();
    await expect(page.getByText("Controller confirmed the fault stopped.", { exact: true })).toBeVisible(WAIT);
    expect(await controller.status()).toMatchObject({ state: "recovering", active: null });
    await expect(banner(page)).toContainText("Recovering", WAIT);
    // The approved recovering copy: eyebrow "Recovering", title "Waiting for services" (status/experiment.ts).
    await expect(title(page)).toHaveText("Waiting for services", WAIT);
    expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "stop_requested")).toHaveLength(1);
    expect((await control.calls()).slice(checkpoint.calls).filter((call) => call.method === "DELETE")).toHaveLength(1);
    await control.setHealth({ "payments-service": "healthy" });
    await idle(page, controller);
  });
}

test("row 5: a failed start is uncertain, read back once without retry, and Stop again clears it", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  await control.mode("payments", "fail");
  await preparePayment(page);
  const response = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/faults/activate", WAIT);
  await inject(page).click();
  const failed = await response;
  expect(failed.status()).toBe(503);
  expect(await failed.json()).toMatchObject({ error_code: "target_unavailable" });
  await expect(manual(page)).toContainText("The affected service didn’t acknowledge the start. The fault may be running: check status before trying again.", WAIT);
  await expect(page.getByText("Controller confirmed the fault is active.", { exact: true })).toHaveCount(0);
  await expect(title(page)).toHaveText("Unknown", WAIT);
  await expect(stop(page, "Stop again")).toBeVisible(WAIT);
  expect(await controller.status()).toMatchObject({ state: "unknown", active: { status: "activation_unconfirmed" } });
  const requests = (await control.requests()).slice(checkpoint.requests);
  const activation = requests.findIndex((request) => request.method === "POST" && request.path === "/faults/activate");
  expect(activation).toBeGreaterThanOrEqual(0);
  expect(requests.slice(activation + 1).some((request) => request.method === "GET" && request.path === "/faults/status")).toBe(true);
  expect(requests.filter((request) => request.method === "POST")).toHaveLength(1);
  expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "fault_requested")).toHaveLength(1);
  expect((await control.calls()).slice(checkpoint.calls)).toMatchObject([{ target: "payments", method: "PUT" }]);
  await control.mode("payments", "ack");
  await stop(page, "Stop again").click();
  await expect(page.getByText("Controller confirmed the fault stopped.", { exact: true })).toBeVisible(WAIT);
  await idle(page, controller);
  expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "stop_requested")).toHaveLength(1);
  expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(["PUT", "DELETE"]);
});

test("row 6: a failed stop stays Unknown; Check status only reads and Stop again succeeds", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  await startPayment(page);
  await control.mode("payments", "fail");
  const response = page.waitForResponse((response) => response.request().method() === "POST" && new URL(response.url()).pathname === "/faults/deactivate", WAIT);
  await stop(page).click();
  const failed = await response;
  expect(failed.status()).toBe(503);
  expect(await failed.json()).toMatchObject({ error_code: "target_unavailable" });
  await expect(title(page)).toHaveText("Unknown", WAIT);
  await expect(page.getByText("The affected service didn’t acknowledge the stop. The fault may still be running: check status, then stop again.", { exact: true })).toBeVisible(WAIT);
  await expect(stop(page, "Stop again")).toBeVisible(WAIT);
  expect(await controller.status()).toMatchObject({ state: "unknown", active: { status: "deactivation_unconfirmed" } });
  const beforeCheck = (await control.requests()).length;
  const read = page.waitForResponse((response) => response.request().method() === "GET" && new URL(response.url()).pathname === "/faults/status", WAIT);
  await banner(page).getByRole("button", { name: "Check status", exact: true }).click();
  expect((await read).status()).toBe(200);
  const checked = (await control.requests()).slice(beforeCheck);
  expect(checked.some((request) => request.method === "GET" && request.path === "/faults/status")).toBe(true);
  expect(checked.filter((request) => request.method === "POST")).toEqual([]);
  expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "stop_requested")).toHaveLength(1);
  await control.mode("payments", "ack");
  // A deliberate second stop: the page ignores stop-like presses for 800 ms after the action ENDS (the read-back
  // that follows the POST is part of the action), so wait that out from the observed end of the busy state, not
  // from the POST response; nothing is sent meanwhile. The button leaves aria-busy in the same render that follows
  // the page starting its cooldown, so an observation of that moment is never earlier than the cooldown's start.
  await expect(stop(page, "Stop again")).not.toHaveAttribute("aria-busy", "true", WAIT);
  await expect(stop(page, "Stop again")).not.toHaveAttribute("aria-disabled", "true", WAIT);
  await page.waitForTimeout(PAGE_STOP_COOLDOWN_MS + COOLDOWN_MARGIN_MS);
  await stop(page, "Stop again").click();
  await expect(page.getByText("Controller confirmed the fault stopped.", { exact: true })).toBeVisible(WAIT);
  await idle(page, controller);
  const events = await activityEvents(controller, checkpoint.since);
  expect(events.filter((event) => event.event === "stop_requested")).toHaveLength(2);
  expect(events.filter((event) => event.event === "stop_unconfirmed")).toHaveLength(1);
  expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(["PUT", "DELETE", "DELETE"]);
});

for (const state of ["manual", "recovering", "unknown"] as const) {
  test(`row 7: ${state} locks Inject and refuses a second activation with its exact 409`, async ({ page, control, controller }) => {
    const checkpoint = await mark(controller, control);
    await openOperator(page, "Idle");
    if (state === "unknown") await control.mode("payments", "fail");
    const setup = await browserPost(page, "/faults/activate", payment);
    expect(setup.status).toBe(state === "unknown" ? 503 : 200);
    if (state === "recovering") {
      await control.setHealth({ "payments-service": "degraded" });
      await controller.deactivate("payments_errors");
    }
    await expect.poll(async () => (await controller.status()).state, WAIT).toBe(state);
    await expect(banner(page)).toHaveAttribute("data-kind", state, WAIT);
    const lockedInject = manual(page).getByRole("button", { name: "Inject fault", includeHidden: true, exact: true });
    await expect(lockedInject).toHaveAttribute("aria-disabled", "true", WAIT);
    const reasons = {
      manual: "One manual fault at a time. Stop the active fault to change parameters.",
      recovering: "Unavailable while services recover from the last fault.",
      unknown: "Unavailable until the fault status is confirmed.",
    };
    // Locked phone disclosures collapse automatically; the lock reason is still rendered.
    await expect(manual(page)).toContainText(reasons[state], WAIT);
    const before = await controller.status();
    const refused = await browserPost(page, "/faults/activate", { scenario: "bad_deployment", duration_seconds: 30 });
    const code = { manual: "fault_conflict", recovering: "recovering", unknown: "status_unknown" }[state];
    expect(refused.status).toBe(409);
    expect(refused.body.error_code).toBe(code);
    const after = await controller.status();
    expect({ ...after, now: 0 }).toEqual({ ...before, now: 0 });
    const events = await activityEvents(controller, checkpoint.since);
    expect(events.filter((event) => event.event === "fault_requested")).toHaveLength(1);
    expect(events.filter((event) => event.event === "control_rejected")).toEqual([expect.objectContaining({ action: "activate", error_code: code })]);
    expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(state === "recovering" ? ["PUT", "DELETE"] : ["PUT"]);
  });
}

test("row 8: real 422 and the UI duration error both leave Idle without an activation", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  await preparePayment(page);
  const invalid = await browserPost(page, "/faults/activate", { ...payment, duration_seconds: 0 });
  expect(invalid.status).toBe(422);
  expect(invalid.body.error_code).toBe("validation_error");
  const beforeUI = (await control.requests()).length;
  await field(page, "Duration").fill("0");
  await inject(page).click();
  await expect(field(page, "Duration")).toHaveAttribute("aria-invalid", "true", WAIT);
  await expect(manual(page).getByText("Enter 0.1–1800 s.", { exact: true })).toBeVisible(WAIT);
  expect((await control.requests()).slice(beforeUI).filter((request) => request.method === "POST")).toEqual([]);
  expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "fault_requested")).toHaveLength(0);
  expect((await control.calls()).slice(checkpoint.calls)).toEqual([]);
  await idle(page, controller);
});

test("row 10: a Stop during a held start never displays a confirmed start", async ({ page, control, controller }) => {
  const checkpoint = await mark(controller, control);
  await preparePayment(page);
  await page.evaluate(() => {
    const observed = window as Window & { contractConfirmedStart?: boolean };
    observed.contractConfirmedStart = false;
    const inspect = (text: string | null) => { if (text?.includes("Controller confirmed the fault is active.")) observed.contractConfirmedStart = true; };
    new MutationObserver((records) => {
      inspect(document.body.textContent);
      for (const record of records) {
        inspect(record.oldValue);
        for (const node of record.addedNodes) inspect(node.textContent);
      }
    }).observe(document.body, { subtree: true, childList: true, characterData: true, characterDataOldValue: true });
  });
  await control.mode("payments", "hold");
  await inject(page).click();
  await expect.poll(async () => (await control.calls()).slice(checkpoint.calls).filter((call) => call.method === "PUT").length, { timeout: 2_000 }).toBe(1);
  // The real controller advertises Stop while its target PUT is still awaiting acknowledgement.
  await expect(stop(page)).toBeVisible({ timeout: 2_400 });
  await stop(page).click();
  await control.release();
  await idle(page, controller);
  expect(await page.evaluate(() => (window as Window & { contractConfirmedStart?: boolean }).contractConfirmedStart)).toBe(false);
  const events = await activityEvents(controller, checkpoint.since);
  expect(events.filter((event) => event.event === "fault_requested")).toHaveLength(1);
  expect(events.filter((event) => event.event === "stop_requested")).toHaveLength(1);
  expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(["PUT", "DELETE"]);
});

for (const gesture of ["double click", "held Enter"] as const) {
  test(`row 11: ${gesture} on Inject sends exactly one activation`, async ({ page, control, controller }) => {
    const checkpoint = await mark(controller, control);
    await preparePayment(page);
    await control.mode("payments", "hold");
    if (gesture === "double click") await inject(page).dblclick();
    else {
      await inject(page).focus();
      try {
        await page.keyboard.down("Enter");
        await page.keyboard.down("Enter");
        await page.keyboard.down("Enter");
      } finally { await page.keyboard.up("Enter"); }
    }
    await expect.poll(async () => (await control.calls()).slice(checkpoint.calls).length, { timeout: 2_000 }).toBe(1);
    await control.release();
    await expect(manual(page)).toContainText("Controller confirmed the fault is active.", WAIT);
    expect(await controller.status()).toMatchObject({ state: "manual", active: { scenario: "payments_errors" } });
    expect((await activityEvents(controller, checkpoint.since)).filter((event) => event.event === "fault_requested")).toHaveLength(1);
    expect((await control.calls()).slice(checkpoint.calls)).toMatchObject([{ target: "payments", method: "PUT" }]);
    expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST" && request.path === "/faults/activate")).toHaveLength(1);
  });
}

test("row 11: phone banner and bar Stop within 800 ms send exactly one deactivation", async ({ page, control, controller }) => {
  test.skip(!isPhone(test.info()), "Two Stop surfaces exist only on phones.");
  const checkpoint = await mark(controller, control);
  await startPayment(page);
  await control.mode("payments", "hold");
  await expect(phoneBar(page).getByRole("button", { name: "Stop Payment Errors", exact: true })).toBeVisible(WAIT);
  // Both real DOM controls are pressed in one browser turn, before either answer can remove them.
  const elapsed = await page.evaluate(() => {
    const first = document.querySelector<HTMLButtonElement>('[aria-label="Experiment"] button');
    const second = document.querySelector<HTMLButtonElement>('[aria-label="Run in progress"] button');
    if (!first || !second) throw new Error("Both Stop surfaces must exist");
    const at = performance.now();
    first.click(); second.click();
    return performance.now() - at;
  });
  expect(elapsed).toBeLessThan(800);
  await expect.poll(async () => (await control.calls()).slice(checkpoint.calls).filter((call) => call.method === "DELETE").length, { timeout: 2_000 }).toBe(1);
  await control.release();
  await expect(page.getByText("Controller confirmed the fault stopped.", { exact: true })).toBeVisible(WAIT);
  await idle(page, controller);
  const events = await activityEvents(controller, checkpoint.since);
  expect(events.filter((event) => event.event === "fault_requested")).toHaveLength(1);
  expect(events.filter((event) => event.event === "stop_requested")).toHaveLength(1);
  expect((await control.calls()).slice(checkpoint.calls).map((call) => call.method)).toEqual(["PUT", "DELETE"]);
  expect((await control.requests()).slice(checkpoint.requests).filter((request) => request.method === "POST" && request.path === "/faults/deactivate")).toHaveLength(1);
});
