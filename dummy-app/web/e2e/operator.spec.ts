// P5-A: the Operator Dashboard's header and experiment banner, against the production build with
// typed controller mocks (e2e/fixtures/controller.ts). Every state is captured at 1440, 390 and 375
// beside its artboard (npm run e2e:review). The page only reads: any request but the mocked GETs
// (a POST to start or stop a fault, for example) is a harness violation and fails the test.
import type { Page } from "@playwright/test";
import { CONTROLLER_NOW, controllerStatus, type ControllerState } from "./fixtures/controller.ts";
import { OPERATOR_URL, expect, test, type MockApi, type MockReply } from "./support/harness.ts";

const banner = (page: Page) => page.getByRole("region", { name: "Experiment" });
const title = (page: Page) => banner(page).getByRole("heading", { level: 1 });
const header = (page: Page) => page.getByRole("banner");
const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

/** GET /faults/status answers with `reply()`; returns how many times it was read. */
function serveStatus(api: MockApi, reply: () => MockReply) {
  const reads = { count: 0 };
  api.onController("GET", "/faults/status", () => {
    reads.count += 1;
    return reply();
  });
  return reads;
}

/** Opens the page on the controller's clock and freezes it, so countdowns hold still for the capture. */
async function open(page: Page, expected: string) {
  await page.clock.install({ time: CONTROLLER_NOW * 1000 });
  await page.goto(OPERATOR_URL);
  await expect(title(page)).toHaveText(expected);
  await page.clock.pauseAt(CONTROLLER_NOW * 1000 + 3_000);
  await page.evaluate(() => document.fonts.ready);
}

/**
 * The page's clock is frozen (see open), but mocked answers arrive in real time and React Query
 * applies them on its own timers: step the frozen clock until `read()` gives `expected`.
 */
async function stepUntil<T>(page: Page, read: () => Promise<T>, expected: T) {
  await expect
    .poll(async () => {
      await page.clock.runFor(50);
      return read();
    })
    .toEqual(expected);
}

const STATES: [ControllerState, string, string, RegExp][] = [
  ["idle", "operator-idle", "Idle", /Experiment\s*Idle\s*No fault is running\.\s*No active timer/],
  ["manual", "operator-manual-active", "Payment Errors", /Manual fault · F2\s*Payment Errors\s*Started 13:58:42 · 90% of payment requests fail with HTTP 503\.\s*0[0-3]:\d\d\s*Remaining of 5:00/],
  ["automatic", "operator-automatic-cycle", "Payment Errors", /Automatic cycle · Repeat 2 of 3\s*Payment Errors\s*Fault phase · shuffled · seed 7341\s*0[01]:\d\d\s*Left in this phase/],
  ["recovering", "operator-recovering", "Waiting for services", /Recovering\s*Waiting for services\s*Fault stopped at 13:59:51\. Controls unlock when health checks pass\.\s*No active timer/],
  ["unknown", "operator-status-unknown", "Unknown", /Fault status\s*Unknown\s*The stop request wasn’t confirmed\. The fault may still be running\.\s*Unavailable\s*Check status/],
];

test.describe("experiment banner", () => {
  for (const [state, stateId, heading, text] of STATES) {
    test(`${state}: the controller's state, from GET /faults/status only`, async ({ page, api, capture }) => {
      serveStatus(api, () => ({ status: 200, body: controllerStatus(state) }));
      await open(page, heading);
      await expect(banner(page)).toHaveText(text);
      await expect(header(page).getByText("Controller connected")).toBeAttached();
      expect(await noSideScroll(page)).toBe(true);
      await capture(stateId);
    });
  }

  test("disconnected: the last confirmed state, Retry connection, and back to live once it answers", async ({ page, api, capture }) => {
    let up = false;
    serveStatus(api, () => (up ? { status: 200, body: controllerStatus("idle") } : "network-error"));
    await open(page, "Unreachable");
    await expect(banner(page)).toContainText("Can’t reach the local controller. Live state is unknown; nothing has been confirmed yet.");
    await expect(header(page).getByText("Controller disconnected")).toBeAttached();
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-disconnected");
    up = true;
    const retry = banner(page).getByRole("button", { name: "Retry connection" });
    await retry.focus();
    await page.keyboard.press("Enter");
    await stepUntil(page, () => title(page).textContent(), "Idle");
    await expect(title(page)).toBeFocused();
    await expect(header(page).getByText("Controller connected")).toBeAttached();
  });

  test("a running fault the controller stops answering about is shown as last confirmed, never counted down", async ({ page, api }) => {
    let up = true;
    serveStatus(api, () => (up ? { status: 200, body: controllerStatus("manual") } : { status: 503, body: { error_code: "unavailable", message: "x", request_id: "ctl-1" } }));
    await open(page, "Payment Errors");
    up = false;
    await page.clock.runFor(2500);
    await stepUntil(page, () => title(page).textContent(), "Unreachable");
    await expect(banner(page)).toContainText("the last confirmed state was a running fault");
    await expect(banner(page)).not.toContainText(/\d\d:\d\d/);
  });

  test("Check status in the unknown state reads again with a GET, nothing else", async ({ page, api }) => {
    let state: ControllerState = "unknown";
    const reads = serveStatus(api, () => ({ status: 200, body: controllerStatus(state) }));
    await open(page, "Unknown");
    const before = reads.count;
    state = "recovering";
    await banner(page).getByRole("button", { name: "Check status" }).click();
    await stepUntil(page, () => title(page).textContent(), "Waiting for services");
    // The check itself read once; stepping the clock may have let a regular poll read too.
    expect(reads.count).toBeGreaterThanOrEqual(before + 1);
  });
});

test.describe("header", () => {
  test("wordmark, Operator, the connection and Storefront ↗; on phones the words stay for screen readers", async ({ page, api }, info) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    await expect(header(page).getByRole("img", { name: "OBSERVA" })).toBeVisible();
    await expect(header(page).getByText("Operator", { exact: true })).toBeVisible();
    const link = header(page).getByRole("link", { name: /Storefront/ });
    await expect(link).toHaveAttribute("href", "http://127.0.0.1:18000/");
    const connection = header(page).getByText("Controller connected");
    if (info.project.name === "desktop-1440") {
      await expect(connection).toBeVisible();
      await expect(link).toContainText("Storefront");
    } else {
      // The 390 artboard shows just the dot and the arrow: the words are visually hidden only.
      await expect(connection).toBeAttached();
      expect(await connection.evaluate((el) => el.getBoundingClientRect().width)).toBeLessThanOrEqual(1);
      await expect(link).toHaveAccessibleName(/Storefront/);
    }
  });

  test("reads the status every 2.5 s (pausing while hidden is checked in test/operator-status.test.tsx)", async ({ page, api }) => {
    const reads = serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    const frozen = reads.count;
    for (let read = 1; read <= 3; read++) {
      await page.clock.runFor(2500);
      await stepUntil(page, async () => reads.count - frozen >= read, true);
    }
  });
});

test.describe("accessibility", () => {
  test("keyboard: Tab reaches Storefront then the banner's action, each with a visible focus ring", async ({ page, api }) => {
    serveStatus(api, () => "network-error");
    await open(page, "Unreachable");
    const link = header(page).getByRole("link", { name: /Storefront/ });
    const retry = banner(page).getByRole("button", { name: "Retry connection" });
    await page.keyboard.press("Tab");
    await expect(link).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(retry).toBeFocused();
    for (const target of [link, retry]) {
      await target.focus();
      const ring = await target.evaluate((el) => {
        const style = getComputedStyle(el);
        return style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2;
      });
      expect(ring).toBe(true);
    }
  });

  test("on phones every control is at least 44 x 44 px, the arrow-only Storefront link included", async ({ page, api }, info) => {
    test.skip(info.project.name === "desktop-1440", "phone sizes only");
    serveStatus(api, () => "network-error");
    await open(page, "Unreachable");
    for (const control of [header(page).getByRole("link", { name: /Storefront/ }), banner(page).getByRole("button", { name: "Retry connection" })]) {
      const box = await control.boundingBox();
      expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
  });

  test("at 200% zoom (720 CSS px wide) nothing scrolls sideways and nothing is cut off", async ({ page, api }, info) => {
    test.skip(info.project.name !== "desktop-1440", "the desktop page zoomed");
    serveStatus(api, () => ({ status: 200, body: controllerStatus("manual") }));
    await page.setViewportSize({ width: 720, height: 450 });
    await open(page, "Payment Errors");
    expect(await noSideScroll(page)).toBe(true);
    for (const text of ["Payment Errors", "Remaining of 5:00"]) {
      const part = banner(page).getByText(text, { exact: true });
      await part.scrollIntoViewIfNeeded();
      await expect(part).toBeInViewport({ ratio: 1 });
    }
  });
});
