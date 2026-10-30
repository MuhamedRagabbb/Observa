// Fixture self-tests. Run only in explicitly configured contract mode, after human approval.
import type { Page } from "@playwright/test";
import { CONTRACT, expect, test, type StubControl } from "./support/contract.ts";
import { redirectViolation } from "./support/redirects.ts";

function expectOnlyViolations(
  violations: { offOrigin: string[]; leaks: string[] },
  expected: (entry: string) => boolean,
  requireOne = true,
) {
  expect(violations.offOrigin.filter((entry) => !expected(entry)), "unrelated isolation violations").toEqual([]);
  expect(violations.leaks).toEqual([]);
  if (requireOne) expect(violations.offOrigin.length).toBeGreaterThan(0);
}

// A one-use nonce authorizes this simple no-cors GET: a custom header/preflight could hide a missing boundary.
async function followRedirect(page: Page, url: string): Promise<"ok" | "error"> {
  return page.evaluate(async (target) => {
    try {
      await fetch(target, { mode: "no-cors", redirect: "follow" });
      return "ok";
    } catch {
      return "error";
    }
  }, url);
}

test("the routed layer refuses the redirect before delivering it to Chromium", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  // Prove the route handler alone: delivering a 302 would now reach the exempt sentinel and its ledger.
  await isolation.relaxBoundaryForSentinel();
  const redirectUrl = `${CONTRACT.controller}/__harness__/redirect?to=${encodeURIComponent(`${CONTRACT.control}/sentinel`)}&status=302&nonce=${await control.redirectNonce()}`;
  expect(await followRedirect(page, redirectUrl)).toBe("error");
  const violations = await isolation.takeViolations();
  expect(violations.offOrigin).toEqual([
    redirectViolation(redirectUrl, 302, { location: `${CONTRACT.control}/sentinel` }),
  ]);
  expect(violations.leaks).toEqual([]);
  expect(await control.sentinelHits()).toBe(0);
  expect(await control.sentinelContacts()).toEqual([]);
  const redirects = (await control.requests()).filter((entry) => entry.path === "/__harness__/redirect");
  expect(redirects).toHaveLength(1);
  expect(redirects[0]?.reissued).toBe("route.fetch");
});

test("the browser boundary prevents a genuine redirect from reaching the sentinel", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  const destination = `${CONTRACT.control}/sentinel`;
  const redirectUrl = `${CONTRACT.controller}/__harness__/redirect?to=${encodeURIComponent(destination)}&status=302&nonce=${await control.redirectNonce()}`;
  isolation.genuine("GET", redirectUrl);
  const result = await followRedirect(page, redirectUrl);
  const hits = await control.sentinelHits();
  const contacts = await control.sentinelContacts();
  const violations = await isolation.takeViolations();
  const redirects = (await control.requests()).filter((entry) => entry.path === "/__harness__/redirect");
  test.info().annotations.push({
    type: "boundary-evidence",
    description: JSON.stringify({ result, hits, contacts, entry: redirects[0], offOrigin: violations.offOrigin }),
  });
  expect(result).toBe("error");
  expect(hits).toBe(0);
  expect(contacts).toEqual([]);
  // Chromium may block before Playwright emits the hop; an audit entry is diagnostic, not prevention.
  expectOnlyViolations(violations, (url) => url === destination, false);
  expect(redirects).toHaveLength(1);
  expect(redirects[0]?.reissued).toBeNull();
});

test("a direct page fetch cannot reach the harness sentinel", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  const destination = `${CONTRACT.control}/sentinel`;
  const result = await page.evaluate(async (target) => {
    try {
      await fetch(target);
      return "ok";
    } catch {
      return "error";
    }
  }, destination);
  expect(result).toBe("error");
  expect(await control.sentinelHits()).toBe(0);
  expect(await control.sentinelContacts()).toEqual([]);
  const violations = await isolation.takeViolations();
  expectOnlyViolations(violations, (url) => url === destination, false);
});

test("decoded JSON ground truth is recorded by the browser isolation fixture", async ({ page, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  const statusUrl = `${CONTRACT.controller}/faults/status`;
  // One response leaks intentionally; later polling must receive the real, clean controller answer.
  await page.route(statusUrl, (route) => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify({ state: "idle", root_cause_service: "payments-service" }),
  }), { times: 1 });
  const requested = page.waitForRequest((request) => request.url() === statusUrl);
  const answered = page.waitForResponse((response) => response.url() === statusUrl);
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await requested;
  await (await answered).finished();
  const violations = await isolation.takeViolations();
  expect(violations.leaks).toEqual([`${statusUrl}: root_cause_service`]);
  expect(violations.offOrigin).toEqual([]);
});

test("a WebSocket is closed and recorded before it connects", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  const socketUrl = new URL("/sentinel", CONTRACT.control);
  socketUrl.protocol = "ws:";
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await page.evaluate((url) => new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => reject(new Error("Blocked WebSocket did not close")), 5_000);
    const socket = new WebSocket(url);
    socket.addEventListener("close", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  }), socketUrl.href);
  const violations = await isolation.takeViolations();
  expect(violations.offOrigin).toEqual([`websocket ${socketUrl.href}`]);
  expect(violations.leaks).toEqual([]);
  expect(await control.sentinelHits()).toBe(0);
  expect(await control.sentinelContacts()).toEqual([]);
});

// --- the Node scan of re-issued responses (a response is read whole in Node before Chromium receives it) ---
// The real controller never answers with ground truth, so these use a harness-only probe served by the
// recording wrapper on the controller origin (header-protected, fixed bodies; see operator_contract.py).

const PROBE_HEADERS = { "X-Contract-Harness": "1" };
const PROBE_PATH = "/__harness__/ground-truth-probe";

/** How many probe requests the harness has received (it records each on arrival, before any hold). */
async function heldProbes(control: StubControl): Promise<number> {
  return (await control.requests()).filter((row) => row.path === PROBE_PATH).length;
}

async function probe(page: Page, url: string): Promise<{ delivered: boolean; text: string }> {
  return page.evaluate(async ({ target, headers }) => {
    try {
      const response = await fetch(target, { headers, cache: "no-store" });
      return { delivered: true, text: await response.text() };
    } catch {
      return { delivered: false, text: "" };
    }
  }, { target: url, headers: PROBE_HEADERS });
}

test("a re-issued response the page cancels before it is delivered is scanned in Node and is no violation", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  // Forty held clean probes: the harness answers none of them until released, so every request is provably
  // unfinished when the page cancels it. The page's background polling never uses the probe path, so counting by
  // path counts only this test's requests. The harness holds a probe for 6 s at most: the observation budgets
  // below (3 s + 2 s) end well before that.
  const url = `${CONTRACT.controller}/__harness__/ground-truth-probe?kind=clean&hold=1`;
  const mine = (target: string) => new URL(target).pathname === PROBE_PATH;
  const answered: string[] = [];
  const terminal: string[] = []; // "finished" or the failure text of each of this test's requests
  page.on("response", (response) => { if (mine(response.url())) answered.push(response.url()); });
  page.on("requestfinished", (request) => { if (mine(request.url())) terminal.push("finished"); });
  page.on("requestfailed", (request) => { if (mine(request.url())) terminal.push(request.failure()?.errorText ?? "unknown"); });
  const before = isolation.inspections();
  try {
    await page.evaluate(({ target, headers }) => {
      const aborts = Array.from({ length: 40 }, () => new AbortController());
      (window as unknown as { cancelProbes: () => void }).cancelProbes = () => aborts.forEach((abort) => abort.abort());
      for (const abort of aborts) void fetch(target, { headers, cache: "no-store", signal: abort.signal }).catch(() => undefined);
    }, { target: url, headers: PROBE_HEADERS });
    // Barrier, not a sleep: all forty are at the server (held) before the page cancels any of them.
    await expect.poll(() => heldProbes(control), { timeout: 3_000, intervals: [25] }).toBe(40);
    await page.evaluate(() => (window as unknown as { cancelProbes: () => void }).cancelProbes());
    await expect.poll(() => terminal.length, { timeout: 2_000, intervals: [25] }).toBe(40);
    // Guaranteed by the hold rather than lucky: nothing was delivered, and Chromium reported every one cancelled.
    expect(answered).toEqual([]);
    expect(terminal, "every request must have been cancelled by the page (net::ERR_ABORTED)").toEqual(Array(40).fill("net::ERR_ABORTED"));
  } finally {
    await control.release(); // never leave a held probe behind, whatever failed above
  }
  // The handlers finish their Node scan after the page is gone: each of the forty was scanned whole, none needed
  // the browser-side body read that a cancellation makes impossible.
  await expect.poll(() => isolation.inspections().nodeScanned - before.nodeScanned, { timeout: 10_000 }).toBeGreaterThanOrEqual(40);
  await expect.poll(() => isolation.inFlight(), { timeout: 10_000 }).toBe(0);
  expect(isolation.inspections().browserReads - before.browserReads).toBe(0);
  expect(await isolation.takeViolations()).toEqual({ offOrigin: [], leaks: [] });
});

test("ground truth in a re-issued response is withheld from the browser and recorded", async ({ page, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  const url = `${CONTRACT.controller}/__harness__/ground-truth-probe`;
  expect(await probe(page, url)).toEqual({ delivered: false, text: "" });
  const violations = await isolation.takeViolations();
  expect(violations.leaks).toEqual([`${url}: root_cause_service`]);
  expect(violations.offOrigin).toEqual([]);
});

test("a re-issued JSON response that cannot be read is withheld and recorded (fail-closed)", async ({ page, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  const url = `${CONTRACT.controller}/__harness__/ground-truth-probe?kind=invalid`;
  expect(await probe(page, url)).toEqual({ delivered: false, text: "" });
  const violations = await isolation.takeViolations();
  expect(violations.leaks).toEqual([`JSON inspection failed for ${url}: body is not valid JSON`]);
  expect(violations.offOrigin).toEqual([]);
});

test("a genuine Chromium pass is still scanned in the browser: ground truth is detected, not prevented", async ({ page, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  const url = `${CONTRACT.controller}/__harness__/ground-truth-probe`;
  isolation.genuine("GET", url);
  const before = isolation.inspections();
  const answer = await probe(page, url);
  expect(answer.delivered).toBe(true);
  expect(JSON.parse(answer.text)).toEqual({ state: "idle", root_cause_service: "harness-probe" });
  const violations = await isolation.takeViolations();
  // The strict browser-side read happened, and the Node scan did not stand in for it.
  expect(isolation.inspections().browserReads - before.browserReads).toBe(1);
  expect(violations.leaks).toEqual([`${url}: root_cause_service`]);
  expect(violations.offOrigin).toEqual([]);
});

test("an inspection still running when the test ends is joined before the verdict", async ({ page, control, isolation }) => {
  if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
  await page.goto(`${CONTRACT.controller}/ui/next/`);
  await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
  // The harness holds this probe's answer until the test releases it (6 s at most; the steps below take well under
  // 4 s), so its Node inspection is provably still pending when the verdict is asked for. The fetch is not
  // awaited: the page has gone on, as at the end of a test.
  const url = `${CONTRACT.controller}${PROBE_PATH}?kind=leak&hold=1`;
  let verdict: ReturnType<typeof isolation.takeViolations> | undefined;
  try {
    await page.evaluate(({ target, headers }) => { void fetch(target, { headers, cache: "no-store" }).catch(() => undefined); }, { target: url, headers: PROBE_HEADERS });
    // The harness records the probe on arrival and then holds it: this is THIS request, held and active.
    await expect.poll(() => heldProbes(control), { timeout: 3_000, intervals: [25] }).toBe(1);
    // Its handler is therefore inside route.fetch, so a pending inspection exists (background polls cannot stand in
    // for it: the request is already proven by the harness log above).
    expect(isolation.inFlight()).toBeGreaterThan(0);
    verdict = isolation.takeViolations();
    const early = await Promise.race([verdict.then(() => "resolved"), new Promise<string>((resolve) => setTimeout(() => resolve("pending"), 500))]);
    expect(early, "the verdict must wait for the held inspection").toBe("pending");
  } finally {
    await control.release(); // the held request is always released, whatever failed above
  }
  const violations = await verdict;
  expect(violations?.leaks).toEqual([`${url}: root_cause_service`]);
  expect(violations?.offOrigin).toEqual([]);
});

// Compressed answers: Node scans the decoded bytes it will hand to Chromium; the browser must see the same value.
for (const encoding of ["gzip", "deflate"] as const) {
  test(`a ${encoding} JSON response reaches the browser as the value Node scanned`, async ({ page, isolation }) => {
    if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
    await page.goto(`${CONTRACT.controller}/ui/next/`);
    await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
    const url = `${CONTRACT.controller}/__harness__/ground-truth-probe?kind=clean&encoding=${encoding}`;
    const answer = await probe(page, url);
    expect(answer.delivered).toBe(true);
    expect(JSON.parse(answer.text)).toEqual({ state: "idle", probe: "clean" });
    expect(await isolation.takeViolations()).toEqual({ offOrigin: [], leaks: [] });
  });

  test(`ground truth in a ${encoding} JSON response is withheld and recorded`, async ({ page, isolation }) => {
    if (!CONTRACT) throw new Error("Contract tests need E2E_CONTRACT=1");
    await page.goto(`${CONTRACT.controller}/ui/next/`);
    await expect(page.getByRole("heading", { name: "Idle", exact: true })).toBeVisible();
    const url = `${CONTRACT.controller}/__harness__/ground-truth-probe?kind=leak&encoding=${encoding}`;
    expect(await probe(page, url)).toEqual({ delivered: false, text: "" });
    const violations = await isolation.takeViolations();
    expect(violations.leaks).toEqual([`${url}: root_cause_service`]);
    expect(violations.offOrigin).toEqual([]);
  });
}
