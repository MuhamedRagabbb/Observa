import type { Page, TestInfo } from "@playwright/test";
import { CONTRACT, expect, type ContractController } from "./contract.ts";
import { sectionName } from "./region-names.ts";

export const banner = (page: Page) => page.getByRole("region", { name: "Experiment", exact: true });
export const title = (page: Page) => banner(page).getByRole("heading", { level: 1 });
export const manual = (page: Page) => page.getByRole("region", { name: sectionName("Manual fault injection") });
export const inject = (page: Page) => manual(page).getByRole("button", { name: "Inject fault", exact: true });
export const scenario = (page: Page, name: string) => manual(page).getByRole("radio", { name, exact: true });
export const field = (page: Page, name: string) => manual(page).getByRole("textbox", { name, exact: true });
export const cyclePanel = (page: Page) => page.getByRole("region", { name: sectionName("Automatic cycle") });
export const startCycle = (page: Page) => cyclePanel(page).getByRole("button", { name: "Start cycle", exact: true });
export const phoneBar = (page: Page) => page.getByRole("region", { name: "Run in progress", exact: true });

export async function openOperator(page: Page, expectedTitle: string) {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(title(page)).toHaveText(expectedTitle, { timeout: 10_000 });
  await expect(page.getByRole("banner").getByText("Controller connected", { exact: true })).toBeVisible({ timeout: 10_000 });
}

export async function expandAll(page: Page) {
  // A locked phone disclosure deliberately stays collapsed; inspect its controls with
  // includeHidden where a row requires evidence about the locked form.
  const toggles = page.locator("button[data-disclosure-toggle][aria-expanded='false']");
  for (const toggle of await toggles.all()) {
    const section = toggle.locator("xpath=ancestor::section[1]");
    if (await section.locator("input[readonly][aria-disabled='true']").count() === 0) await toggle.click();
  }
}

/** /activity is newest first. Marks are the server's event timestamps, never the browser clock. */
export async function activityEvents(controller: ContractController, since?: string) {
  const { entries } = await controller.activity();
  return since === undefined ? entries : entries.filter((entry) => entry.at > since);
}

export const isPhone = (info: TestInfo) => /^contract-mobile-(390|375)$/.test(info.project.name);
