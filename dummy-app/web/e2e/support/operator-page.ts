// What the Operator Dashboard's mocked browser specs share: page locators, the default controller
// answers, opening the page on the controller's clock, and stepping the frozen clock.
import type { Page } from "@playwright/test";
import { activity, CONTROLLER_NOW, healthSnapshot } from "../fixtures/controller.ts";
import { OPERATOR_URL, expect, type MockApi, type MockReply } from "./harness.ts";

export const banner = (page: Page) => page.getByRole("region", { name: "Experiment" });
export const title = (page: Page) => banner(page).getByRole("heading", { level: 1 });
export const header = (page: Page) => page.getByRole("banner");
export const noSideScroll = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

/** GET /services/health and /activity: healthy and empty, unless a test registers its own answer after this. */
export function serveMonitoring(api: MockApi) {
  api.onController("GET", "/services/health", () => ({ status: 200, body: healthSnapshot() }));
  api.onController("GET", "/activity", () => ({ status: 200, body: activity([]) }));
}

/** GET /faults/status answers with `reply()`; returns how many times it was read. */
export function serveStatus(api: MockApi, reply: () => MockReply) {
  serveMonitoring(api);
  const reads = { count: 0 };
  api.onController("GET", "/faults/status", () => {
    reads.count += 1;
    return reply();
  });
  return reads;
}

/** Opens the page on the controller's clock and freezes it, so countdowns hold still for the capture. */
export async function open(page: Page, expected: string) {
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
export async function stepUntil<T>(page: Page, read: () => Promise<T>, expected: T) {
  await expect
    .poll(async () => {
      await page.clock.runFor(50);
      return read();
    })
    .toEqual(expected);
}
