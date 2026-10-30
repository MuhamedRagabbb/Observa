// Header evidence comes from the controller-side recorder. Only explicit passes exercise Chromium.
import { CONTRACT, expect, test } from "./support/contract.ts";

test("requests the fixture re-issues are labelled and are not browser-header evidence", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  // Discard the Node cleanup requests made by the automatic clean fixture before this test.
  await control.reset();
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  expect(await isolation.takeViolations()).toEqual({ offOrigin: [], leaks: [] });
  const recorded = await control.requests();
  const status = recorded.find((entry) => entry.method === "GET" && entry.path === "/faults/status");
  expect(status).toBeDefined();
  expect(recorded.every((entry) => entry.reissued === "route.fetch")).toBe(true);
  test.info().annotations.push({ type: "header-evidence", description: JSON.stringify(status) });
});

test("a genuine Chromium POST carries the browser's own Origin and Sec-Fetch headers", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await control.reset();
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  isolation.genuine("POST", `${CONTRACT.controller}/faults/auto/stop`);
  const status = await page.evaluate(async () => (await fetch("/faults/auto/stop", { method: "POST" })).status);
  expect(status).toBe(200);
  const entry = (await control.requests()).find((row) => row.method === "POST" && row.path === "/faults/auto/stop");
  expect(entry).toBeDefined();
  test.info().annotations.push({ type: "header-evidence", description: JSON.stringify(entry) });
  expect(entry).toMatchObject({
    reissued: null,
    origin: CONTRACT.controller,
    host: new URL(CONTRACT.controller).host,
    sec_fetch_site: "same-origin",
    sec_fetch_mode: "cors",
  });
  expect(await isolation.takeViolations()).toEqual({ offOrigin: [], leaks: [] });
});

test("the same POST without a pass is re-issued and labelled", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await control.reset();
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  const status = await page.evaluate(async () => (await fetch("/faults/auto/stop", { method: "POST" })).status);
  expect(status).toBe(200);
  const entry = (await control.requests()).find((row) => row.method === "POST" && row.path === "/faults/auto/stop");
  expect(entry).toBeDefined();
  test.info().annotations.push({ type: "header-evidence", description: JSON.stringify(entry) });
  expect(entry?.reissued).toBe("route.fetch");
  expect(await isolation.takeViolations()).toEqual({ offOrigin: [], leaks: [] });
});
