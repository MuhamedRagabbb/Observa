// Smoke only: real operator bundle and controller reads, with no injected faults.
import { readFileSync } from "node:fs";
import { CONTRACT, expect, test } from "./support/contract.ts";

const livePorts = new Set<number>((JSON.parse(readFileSync(new URL("../contracts/stack-ports.json", import.meta.url), "utf8")) as { live: number[] }).live);

test("the real controller serves the operator and publishes no live ports", async ({ page, controller }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  await expect(page.getByRole("banner").getByText("Controller connected", { exact: true })).toBeVisible();
  const config = await controller.config();
  for (const port of Object.values(config.ports)) expect(livePorts.has(port), `published live port ${port}`).toBe(false);
});

test("idle browsing only reads controller APIs and reports no console errors", async ({ page }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  const reads: { method: string; path: string }[] = [];
  const errors: string[] = [];
  const paths = ["/faults/status", "/services/health", "/activity", "/ui/config"];
  page.on("request", (request) => {
    // The document, scripts, fonts and styles are also reads; only API requests belong here.
    if (["fetch", "xhr"].includes(request.resourceType())) reads.push({ method: request.method(), path: new URL(request.url()).pathname });
    else if (request.method() !== "GET") errors.push(`${request.method()} ${request.url()}`);
  });
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${CONTRACT.controller}/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  await expect(page.getByRole("banner").getByText("Controller connected", { exact: true })).toBeVisible();
  await expect.poll(() => [...new Set(reads.map((read) => read.path))].sort()).toEqual([...paths].sort());
  expect(reads.every((read) => read.method === "GET" && paths.includes(read.path))).toBe(true);
  expect(errors).toEqual([]);
});
