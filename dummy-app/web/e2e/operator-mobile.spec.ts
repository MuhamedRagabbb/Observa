// P5-E: the Operator Dashboard on phones (390 and 375), against the production build with typed
// controller mocks. Collapsible Manual fault injection / Automatic cycle sections with one-line
// summaries, and the sticky bottom Stop bar while a fault or cycle runs. On desktop nothing changes.
// Every POST is answered by a mock and each test counts the POSTs it caused.
import type { Locator, Page } from "@playwright/test";
import { controllerStatus, type ControllerState } from "./fixtures/controller.ts";
import { expect, test, type MockApi } from "./support/harness.ts";
import { banner, noSideScroll, open, serveStatus, stepUntil, title } from "./support/operator-page.ts";

const toggle = (page: Page, name: string) => page.getByRole("button", { name: new RegExp(`^${name}`) });
const manual = (page: Page) => page.getByRole("region", { name: "Manual fault injection" });
const cycle = (page: Page) => page.getByRole("region", { name: "Automatic cycle" });
const bar = (page: Page) => page.getByRole("region", { name: "Run in progress" });
const posts = (api: MockApi) => api.calls.filter((call) => call.method === "POST");

function controller(api: MockApi, initial: ControllerState) {
  const live = { state: initial };
  serveStatus(api, () => ({ status: 200, body: controllerStatus(live.state) }));
  api.onController("POST", "/faults/deactivate", () => {
    live.state = "recovering";
    return { status: 200, body: controllerStatus("recovering") };
  });
  api.onController("POST", "/faults/auto/stop", () => {
    live.state = "recovering";
    return { status: 200, body: controllerStatus("recovering") };
  });
  return live;
}

const heights = async (...controls: Locator[]) => Promise.all(controls.map(async (control) => (await control.boundingBox())?.height ?? 0));

test.describe("phones", () => {
  test.beforeEach(async ({}, info) => {
    test.skip(info.project.name === "desktop-1440", "phone sizes only");
  });

  test("idle: Manual fault injection is open with its summary, Automatic cycle is collapsed and out of reach", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    await expect(toggle(page, "Manual fault injection")).toHaveAttribute("aria-expanded", "true");
    await expect(toggle(page, "Manual fault injection")).toContainText("F2 · 90% · HTTP 503 · 120 s");
    await expect(toggle(page, "Automatic cycle")).toHaveAttribute("aria-expanded", "false");
    await expect(toggle(page, "Automatic cycle")).toContainText("1 repeat · 12 min 30 s");
    await expect(cycle(page).getByRole("button", { name: "Start cycle" })).toHaveCount(0);
    await expect(manual(page).getByRole("button", { name: "Inject fault" })).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    for (const height of await heights(toggle(page, "Manual fault injection"), toggle(page, "Automatic cycle"))) expect(height).toBeGreaterThanOrEqual(44);
    await expect(bar(page)).toHaveCount(0);
  });

  test("the disclosure buttons work from the keyboard, keep focus and reveal the section", async ({ page, api }) => {
    controller(api, "idle");
    await open(page, "Idle");
    const button = toggle(page, "Automatic cycle");
    await button.focus();
    await page.keyboard.press("Enter");
    await expect(button).toHaveAttribute("aria-expanded", "true");
    await expect(button).toBeFocused();
    await expect(cycle(page).getByRole("button", { name: "Start cycle" })).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await page.keyboard.press("Space");
    await expect(button).toHaveAttribute("aria-expanded", "false");
    await expect(button).toBeFocused();
    await expect(cycle(page).getByRole("button", { name: "Start cycle" })).toHaveCount(0);
  });

  test("a running fault: both sections collapsed and locked, the sticky bar at the bottom, Stop sends one stop", async ({ page, api }) => {
    controller(api, "manual");
    await open(page, "Payment Errors");
    await expect(toggle(page, "Manual fault injection")).toHaveAttribute("aria-expanded", "false");
    await expect(toggle(page, "Manual fault injection")).toContainText(/Locked · F2 · \d+% · HTTP \d+ · [\d.]+ s/);
    await expect(toggle(page, "Automatic cycle")).toContainText("Locked · 1 repeat · 12 min 30 s");
    await expect(bar(page)).toBeVisible();
    await expect(bar(page)).toContainText("Payment Errors");
    await expect(bar(page)).toContainText(/\d\d:\d\d left/);
    const viewport = page.viewportSize()!;
    const box = (await bar(page).boundingBox())!;
    expect(Math.abs(box.y + box.height - viewport.height)).toBeLessThanOrEqual(1);
    expect(box.width).toBeGreaterThanOrEqual(viewport.width - 1);
    const stop = bar(page).getByRole("button", { name: "Stop Payment Errors" });
    expect((await stop.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    await expect(banner(page).getByRole("button", { name: "Stop fault" })).toBeVisible();
    expect(await noSideScroll(page)).toBe(true);
    await stop.focus();
    await page.keyboard.press("Enter");
    await stepUntil(page, () => title(page).textContent(), "Waiting for services");
    expect(posts(api)).toHaveLength(1);
    expect(posts(api)[0]).toMatchObject({ path: "/faults/deactivate", body: { scenario: "payments_errors" } });
    await expect(bar(page)).toHaveCount(0);
    await expect(title(page)).toBeFocused();
  });

  test("the bar never covers the end of the page", async ({ page, api }) => {
    controller(api, "manual");
    await open(page, "Payment Errors");
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const last = page.getByRole("listitem").filter({ hasText: "Traffic controls with safe operating limits" });
    const lastBox = (await last.boundingBox())!;
    const barBox = (await bar(page).boundingBox())!;
    expect(lastBox.y + lastBox.height).toBeLessThanOrEqual(barBox.y + 1);
  });

  test("a running cycle: locked summary from the controller's schedule and a Stop for the cycle", async ({ page, api }) => {
    controller(api, "automatic");
    await open(page, "Payment Errors");
    await expect(toggle(page, "Automatic cycle")).toContainText("Locked · 3 repeats · 31 min · shuffled");
    await expect(bar(page)).toContainText(/Cycle · Payment Errors/);
    await expect(banner(page).getByRole("button", { name: "Stop cycle" })).toBeVisible();
    await bar(page).getByRole("button", { name: "Stop automatic cycle" }).click();
    await stepUntil(page, () => title(page).textContent(), "Waiting for services");
    expect(posts(api)).toHaveLength(1);
    expect(posts(api)[0]).toMatchObject({ path: "/faults/auto/stop" });
  });

  test("the bar has no motion when the system asks for reduced motion", async ({ page, api }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    controller(api, "manual");
    await open(page, "Payment Errors");
    const durations = await bar(page).evaluate((element) => [element, ...element.querySelectorAll("*")].map((node) => getComputedStyle(node).transitionDuration + getComputedStyle(node).animationDuration));
    for (const duration of durations) expect(duration).toMatch(/^(0s)+$/);
  });

  for (const state of ["recovering", "unknown", "idle"] as const) {
    test(`${state}: no bar, sections follow the controller's lock`, async ({ page, api }) => {
      controller(api, state);
      await open(page, state === "idle" ? "Idle" : state === "unknown" ? "Unknown" : "Waiting for services");
      await expect(bar(page)).toHaveCount(0);
      expect(await noSideScroll(page)).toBe(true);
      await expect(toggle(page, "Manual fault injection")).toHaveAttribute("aria-expanded", state === "idle" ? "true" : "false");
    });
  }
});

test("desktop: no disclosure buttons, no bar, both sections visible", async ({ page, api }, info) => {
  test.skip(info.project.name !== "desktop-1440", "the desktop layout");
  controller(api, "manual");
  await open(page, "Payment Errors");
  await expect(page.getByRole("button", { name: /^Manual fault injection/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Automatic cycle/ })).toHaveCount(0);
  await expect(bar(page)).toHaveCount(0);
  await expect(manual(page).getByRole("button", { name: "Inject fault" })).toBeVisible();
  await expect(cycle(page).getByRole("button", { name: "Start cycle" })).toBeVisible();
});
