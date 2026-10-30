// Mocked-mode regression self-test: no E2E_* variables, real controller, or external writes.
// All three mocked viewports run this file. Provenance is test isolation, not security:
// it cannot remove races with external controller clients.
import type { Page, Request } from "@playwright/test";
import { activity, controllerStatus, healthSnapshot } from "./fixtures/controller.ts";
import { OPERATOR_ORIGIN, OPERATOR_URL, expect, test as harnessTest, type MockApi, type MockReply } from "./support/harness.ts";
import { openExpanded, stepUntil } from "./support/operator-page.ts";
import { armUiAction, installMutationBoundary } from "./support/t2-boundary.ts";
import { MutationGate, type GateKind } from "./support/t2-evidence.ts";

const PAYMENT = { scenario: "payments_errors", duration_seconds: 120, parameters: { failure_rate: 0.9, status_code: 503 } };
const CANONICAL = [
  { kind: "start-manual", path: "/faults/activate", body: PAYMENT },
  { kind: "stop-manual", path: "/faults/deactivate", body: { scenario: "payments_errors" } },
  { kind: "start-cycle", path: "/faults/auto/start", body: { seed: 7341, scenarios: ["payments_errors"], shuffle: false, warmup_seconds: 0, duration_seconds: 5, recovery_seconds: 5, repeat: 1 } },
  { kind: "stop-cycle", path: "/faults/auto/stop", body: undefined },
] satisfies { kind: GateKind; path: string; body: unknown }[];
type Boundary = { gate: MutationGate; violations: string[] };
type Outcome = number | "rejected" | "opaqueredirect";
type ProbeWindow = Window & {
  __t2Outcomes?: { path: string; outcome: Outcome }[];
  __t2Unrelated?: Promise<Outcome>;
};

// The harness deliberately types only production API paths/methods. These casts let
// regression aliases and PATCH reach a positive mock answer if interception leaks.
// They do not normalize the actual URL/method or bypass MockApi.calls/header recording.
function mock(api: MockApi, method: string, path: string, reply: MockReply) {
  api.onController(method as Parameters<MockApi["onController"]>[0], path as Parameters<MockApi["onController"]>[1], () => reply);
}
const writes = (api: MockApi) => api.calls.filter((call) => !["GET", "HEAD"].includes(call.method));
const inject = (page: Page) => page.getByRole("region", { name: "Manual fault injection" }).getByRole("button", { name: "Inject fault", exact: true });

const test = harnessTest.extend<{ boundary: Boundary }>({
  boundary: [async ({ context, api }, use) => {
    const gate = new MutationGate();
    const violations: string[] = [];
    api.onController("GET", "/faults/status", () => ({ status: 200, body: controllerStatus("idle") }));
    api.onController("GET", "/services/health", () => ({ status: 200, body: healthSnapshot() }));
    api.onController("GET", "/activity", () => ({ status: 200, body: activity([]) }));
    for (const { path } of CANONICAL) mock(api, "POST", path, { status: 200, body: controllerStatus("manual") });
    // Final API: Promise<void>; context routing + init script installed BEFORE navigation.
    // onViolation receives the same refusal reason appended to gate.violations.
    await installMutationBoundary(context, { origin: OPERATOR_ORIGIN, gate, onViolation: (reason) => violations.push(reason) });
    await use({ gate, violations });
  }, { auto: true }],
});
test.use({ serviceWorkers: "block" });

async function originPage(page: Page) {
  await page.goto(new URL("/ui/config", OPERATOR_URL).href);
}
async function post(page: Page, path: string, body: unknown = PAYMENT, method = "POST", keepalive = false): Promise<Outcome> {
  return page.evaluate(async ({ path, body, method, keepalive }) => {
    return fetch(path, { method, body: JSON.stringify(body), headers: { "content-type": "application/json" }, keepalive, redirect: "manual" })
      .then((r) => r.type === "opaqueredirect" ? "opaqueredirect" as const : r.status, () => "rejected" as const);
  }, { path, body, method, keepalive });
}
function blocked(outcome: Outcome, api: MockApi, boundary: Boundary, previousWrites = 0, previousViolations = 0) {
  // Soft assertions expose all three pieces of bypass evidence in the RED report.
  expect.soft(outcome, "the browser fetch must reject").toBe("rejected");
  expect.soft(writes(api), "no refused request may reach a mock handler").toHaveLength(previousWrites);
  expect.soft(boundary.gate.violations, "gate records every refusal").toHaveLength(previousViolations + 1);
  expect.soft(boundary.violations, "onViolation reports every refusal").toEqual(boundary.gate.violations);
}
function stillArmed(gate: MutationGate) {
  expect(() => gate.arm("start-manual", PAYMENT), "a refused request must not consume the slot").toThrow();
}
async function arm(page: Page, gate: MutationGate) {
  // Final API: Promise<string>. Only a subsequent trusted click matching this exact
  // accessible role/name can stamp ONE matching canonical method/path/JSON body.
  const token = await armUiAction(page, gate, { kind: "start-manual", body: PAYMENT, control: { role: "button", name: "Inject fault" } });
  expect(token).toEqual(expect.any(String));
  expect(token.length).toBeGreaterThan(0);
  return token;
}
async function openUi(page: Page) {
  // Same frozen-clock/mobile disclosure expansion helper as operator-manual.spec.ts.
  await openExpanded(page, "Idle");
  // Observe fetch completion without changing body, headers, timing or provenance.
  // The boundary's init script/wrapper was installed first, before navigation.
  await page.evaluate(() => {
    const state = window as ProbeWindow;
    state.__t2Outcomes = [];
    const original = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const method = init?.method ?? (input instanceof Request ? input.method : "GET");
      const path = new URL(url, location.href).pathname;
      try {
        const response = await original(input, init);
        if (method === "POST") state.__t2Outcomes!.push({ path, outcome: response.status });
        return response;
      } catch (error) {
        if (method === "POST") state.__t2Outcomes!.push({ path, outcome: "rejected" });
        throw error;
      }
    };
  });
}
async function outcomes(page: Page, count: number) {
  await expect.poll(() => page.evaluate(() => (window as ProbeWindow).__t2Outcomes!.length)).toBe(count);
  return page.evaluate(() => (window as ProbeWindow).__t2Outcomes!);
}
function provenance(page: Page, token: string) {
  const requests: { path: string; body: unknown; headers: Record<string, string> }[] = [];
  const observe = (request: Request) => {
    if (request.method() !== "POST" || new URL(request.url()).origin !== OPERATOR_ORIGIN) return;
    // Snapshot synchronously at the request event, BEFORE route.fallback({headers})
    // mutates subsequent Request.headers() reads in installed Playwright 1.64.
    requests.push({ path: new URL(request.url()).pathname, body: request.postDataJSON(), headers: { ...request.headers() } });
  };
  page.on("request", observe);
  return () => {
    page.off("request", observe);
    const stamped = requests.filter((r) => r.headers["x-t2-provenance"] === token);
    expect.soft(stamped, "one matching request receives the pending provenance stamp").toHaveLength(1);
    for (const request of stamped) {
      expect(request.path).toBe("/faults/activate");
      expect(request.body).toEqual(PAYMENT);
    }
  };
}
function consumed(gate: MutationGate) {
  expect(() => gate.arm("stop-cycle", undefined), "the authorized request consumed the slot").not.toThrow();
}

test("unsolicited identical POST before the click is refused; the real Inject fault click still succeeds", async ({ page, api, boundary }) => {
  await openUi(page);
  await arm(page, boundary.gate);
  const unsolicited = await post(page, "/faults/activate");
  blocked(unsolicited, api, boundary);
  if (unsolicited !== "rejected") return; // RED already recorded; avoid an unrelated UI timeout in the legacy run.
  stillArmed(boundary.gate);
  await inject(page).click();
  expect((await outcomes(page, 2)).map((r) => r.outcome)).toEqual(["rejected", 200]);
  expect(writes(api)).toHaveLength(1);
  expect(boundary.gate.violations).toHaveLength(1);
  consumed(boundary.gate);
});

for (const entry of CANONICAL) {
  test(`rejects trailing-slash alias ${entry.path}/ while its canonical slot is armed`, async ({ page, api, boundary }) => {
    const alias = `${entry.path}/`;
    // Positive 307 mock exposes the old fall-through. redirect:manual prevents this
    // self-test following a leaked redirect outside the mocked handler chain.
    mock(api, "POST", alias, { status: 307, headers: { location: entry.path } });
    await originPage(page);
    boundary.gate.arm(entry.kind, entry.body);
    const result = await post(page, alias, entry.body);
    blocked(result, api, boundary);
    if (result === "rejected") stillArmed(boundary.gate);
  });
}
test("rejects a noncanonical redirect-style mutation path before forwarding", async ({ page, api, boundary }) => {
  mock(api, "POST", "/redirect-activate", { status: 307, headers: { location: "/faults/activate" } });
  await originPage(page);
  boundary.gate.arm("start-manual", PAYMENT);
  const result = await post(page, "/redirect-activate");
  blocked(result, api, boundary);
  if (result === "rejected") stillArmed(boundary.gate);
});

test("authorized Inject fault POST rejects a 307 without replaying to deactivate", async ({ page, api, boundary }) => {
  mock(api, "POST", "/faults/activate", { status: 307, headers: { location: "/faults/deactivate" } });
  let deactivations = 0;
  api.onController("POST", "/faults/deactivate", () => {
    deactivations++;
    return { status: 200, body: controllerStatus("idle") };
  });
  await openUi(page); // The real handler supplies a Request; retain its body, headers and signal.
  const token = await arm(page, boundary.gate);
  const checkProvenance = provenance(page, token);
  await inject(page).click();
  expect(await outcomes(page, 1)).toEqual([{ path: "/faults/activate", outcome: "rejected" }]);
  expect(writes(api)).toHaveLength(1);
  expect(writes(api)[0]).toMatchObject({ path: "/faults/activate", body: PAYMENT });
  expect(deactivations).toBe(0);
  checkProvenance();
  expect(boundary.gate.violations).toEqual([]); // Native fetch refuses the redirect after authorization.
  expect(boundary.violations).toEqual([]);
  expect(api.pendingViolations()).toEqual([]);
  consumed(boundary.gate);
});

test("requireNoPages rejects an occupied context and permits installation on a fresh one", async ({ browser }) => {
  const occupied = await browser.newContext({ serviceWorkers: "block" });
  const fresh = await browser.newContext({ serviceWorkers: "block" });
  try {
    await occupied.newPage();
    const options = {
      origin: OPERATOR_ORIGIN, gate: new MutationGate(), requireNoPages: true,
      onViolation: (reason: string) => { throw new Error(reason); },
    };
    await expect(installMutationBoundary(occupied, options)).rejects.toThrow("requires a context with no pages");
    expect(fresh.pages()).toHaveLength(0);
    await expect(installMutationBoundary(fresh, options)).resolves.toBeUndefined();
    expect(fresh.pages()).toHaveLength(0);
  } finally { await occupied.close(); await fresh.close(); }
});

test("context boundary installed before any page covers a popup POST and permits its GET", async ({ browser, api }) => {
  const context = await browser.newContext({ serviceWorkers: "block" });
  try {
    await api.install(context);
    const boundary: Boundary = { gate: new MutationGate(), violations: [] };
    expect(context.pages()).toHaveLength(0);
    await installMutationBoundary(context, { origin: OPERATOR_ORIGIN, gate: boundary.gate, requireNoPages: true, onViolation: (reason) => boundary.violations.push(reason) });
    const first = await context.newPage();
    await originPage(first);
    const popupEvent = first.waitForEvent("popup");
    await first.evaluate((url) => { window.open(url); }, new URL("/ui/config", OPERATOR_URL).href);
    const popup = await popupEvent;
    await popup.waitForLoadState("domcontentloaded");
    expect(await popup.evaluate(() => fetch("/faults/status").then((r) => r.status))).toBe(200);
    expect(api.calls.some((r) => r.method === "GET" && r.path === "/faults/status")).toBe(true);
    boundary.gate.arm("start-manual", PAYMENT);
    blocked(await post(popup, "/faults/activate"), api, boundary);
  } finally { await context.close(); }
});

test("untrusted programmatic button.click cannot use provenance; a later locator.click can", async ({ page, api, boundary }) => {
  await openUi(page);
  await arm(page, boundary.gate);
  await inject(page).evaluate((button: HTMLButtonElement) => button.click());
  const result = (await outcomes(page, 1))[0]!.outcome;
  blocked(result, api, boundary);
  if (result !== "rejected") return;
  stillArmed(boundary.gate);
  // Wait for React's readback/release, then advance its known 800ms cooldown;
  // virtual time is not a sleep, and no mutation is retried by the test.
  await stepUntil(page, () => inject(page).getAttribute("aria-busy"), null);
  await page.clock.runFor(801);
  await inject(page).click();
  expect((await outcomes(page, 2)).map((r) => r.outcome)).toEqual(["rejected", 200]);
  expect(writes(api)).toHaveLength(1);
  expect(boundary.gate.violations).toHaveLength(1);
  consumed(boundary.gate);
});

test("dedicated Blob Worker POST has no click provenance and is blocked", async ({ page, api, boundary }) => {
  await originPage(page);
  boundary.gate.arm("start-manual", PAYMENT);
  const result = await page.evaluate(async ({ url, body }) => {
    const source = `onmessage = async ({data}) => { try { const r = await fetch(data.url, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(data.body)}); postMessage(r.status); } catch { postMessage('rejected'); } };`;
    const blob = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
    const worker = new Worker(blob);
    try {
      return await new Promise<Outcome>((resolve, reject) => {
        worker.onmessage = (event: MessageEvent<Outcome>) => resolve(event.data);
        worker.onerror = (event) => reject(new Error(event.message));
        worker.postMessage({ url, body });
      });
    } finally { worker.terminate(); URL.revokeObjectURL(blob); }
  }, { url: `${OPERATOR_ORIGIN}/faults/activate`, body: PAYMENT });
  blocked(result, api, boundary);
  if (result === "rejected") stillArmed(boundary.gate);
});

test("SharedWorker is unavailable before a Blob worker can POST", async ({ page, api, boundary }) => {
  await originPage(page);
  const result = await page.evaluate(({ url, body }) => {
    const source = `onconnect = () => fetch(${JSON.stringify(url)}, {method:'POST', headers:{'content-type':'application/json'}, body:${JSON.stringify(JSON.stringify(body))}});`;
    const blob = URL.createObjectURL(new Blob([source], { type: "text/javascript" }));
    try {
      if (typeof SharedWorker === "undefined") return "unavailable";
      new SharedWorker(blob).port.close();
      return "constructed";
    } catch { return "refused"; }
    finally { URL.revokeObjectURL(blob); }
  }, { url: `${OPERATOR_ORIGIN}/faults/activate`, body: PAYMENT });
  expect(["unavailable", "refused"]).toContain(result);
  expect(writes(api)).toHaveLength(0);
  expect(boundary.gate.violations).toEqual([]); // Explicit API refusal, no routed request.
  expect(boundary.violations).toEqual([]);
});

test("controller keepalive mutations reject with TypeError before reaching the mock", async ({ page, api, boundary }) => {
  await originPage(page);
  boundary.gate.arm("start-manual", PAYMENT);
  const results = await page.evaluate(async (body) => {
    const init = { method: "POST", body: JSON.stringify(body), headers: { "content-type": "application/json" }, keepalive: true };
    const refusal = (error: unknown) => error instanceof TypeError ? error.message : String(error);
    return Promise.all([
      fetch("/faults/activate", init).then(() => "resolved", refusal),
      fetch(new Request(new URL("/faults/activate", location.href), init)).then(() => "resolved", refusal),
    ]);
  }, PAYMENT);
  expect(results).toEqual(["T2 blocks controller keepalive mutations", "T2 blocks controller keepalive mutations"]);
  expect(writes(api)).toHaveLength(0);
  expect(boundary.gate.violations).toEqual([]); // Explicit TypeError, no routed request.
  expect(boundary.violations).toEqual([]);
  stillArmed(boundary.gate);
});

test("controller sendBeacon returns false without sending to the mock", async ({ page, api, boundary }) => {
  await originPage(page);
  boundary.gate.arm("start-manual", PAYMENT);
  expect(await page.evaluate((body) => navigator.sendBeacon("/faults/activate", new Blob([JSON.stringify(body)], { type: "application/json" })), PAYMENT)).toBe(false);
  expect(writes(api)).toHaveLength(0);
  expect(boundary.gate.violations).toEqual([]); // False reports refusal before queueing/network.
  expect(boundary.violations).toEqual([]);
  stillArmed(boundary.gate);
});

test("serviceWorkers:block prevents a registered worker from controlling the page", async ({ page, context, api, boundary }) => {
  let scriptReads = 0;
  await context.route(`${OPERATOR_ORIGIN}/t2-sw.js`, async (route) => {
    scriptReads++;
    await route.fulfill({ contentType: "text/javascript", body: "self.addEventListener('install', e => e.waitUntil(self.skipWaiting())); self.addEventListener('activate', e => e.waitUntil(self.clients.claim())); self.addEventListener('fetch', e => e.respondWith(new Response('bypass')));" });
  });
  await originPage(page);
  const state = await page.evaluate(async () => {
    let rejected = false;
    try { await navigator.serviceWorker.register("/t2-sw.js", { scope: "/" }); }
    catch { rejected = true; }
    return { rejected, controlled: navigator.serviceWorker.controller !== null, registrations: (await navigator.serviceWorker.getRegistrations()).length };
  });
  // Installed Playwright 1.64 replaces register() with a resolved no-op when blocked;
  // rejection is not required, but neither registration nor control may survive.
  expect(state.controlled).toBe(false);
  expect(state.registrations).toBe(0);
  expect(context.serviceWorkers()).toHaveLength(0);
  expect(scriptReads).toBe(0);
  // Also test the mutation boundary after the registration attempt (RED on legacy).
  boundary.gate.arm("start-manual", PAYMENT);
  blocked(await post(page, "/faults/activate"), api, boundary);
});

for (const method of ["PUT", "DELETE", "PATCH"]) {
  for (const path of [...CANONICAL.map((entry) => entry.path), "/faults/status"]) {
    test(`denies ${method} ${path} but keeps monitoring GETs working`, async ({ page, api, boundary }) => {
      mock(api, method, path, { status: 200, body: {} });
      await originPage(page);
      const readPaths = ["/faults/status", "/activity", "/services/health"];
      expect(await page.evaluate(async (paths) => Promise.all(paths.map((p) => fetch(p).then((r) => r.status))), readPaths)).toEqual([200, 200, 200]);
      for (const readPath of readPaths) expect(api.calls.some((r) => r.method === "GET" && r.path === readPath)).toBe(true);
      blocked(await post(page, path, {}, method), api, boundary);
    });
  }
}

test("authorized click stamps provenance in transit and strips all x-t2-* headers before the mock", async ({ page, api, boundary }) => {
  await openUi(page);
  const token = await arm(page, boundary.gate);
  const checkProvenance = provenance(page, token);
  await inject(page).click();
  expect((await outcomes(page, 1))[0]!.outcome).toBe(200);
  expect(writes(api)).toHaveLength(1);
  expect(writes(api)[0]!.body).toEqual(PAYMENT);
  checkProvenance();
  expect(Object.keys(writes(api)[0]!.headers).filter((key) => key.toLowerCase().startsWith("x-t2-"))).toEqual([]);
  expect(boundary.gate.violations).toEqual([]);
  consumed(boundary.gate);
});

test("scripted duplicate after an authorized click is refused and only one POST reaches the mock", async ({ page, api, boundary }) => {
  await openUi(page);
  const token = await arm(page, boundary.gate);
  const checkProvenance = provenance(page, token);
  await inject(page).click();
  expect((await outcomes(page, 1))[0]!.outcome).toBe(200);
  const result = await post(page, "/faults/activate");
  blocked(result, api, boundary, 1);
  expect(boundary.gate.violations[0]).toMatch(/duplicate/i);
  checkProvenance();
});

const interleaved = [
  { name: "different body on the same canonical path", path: "/faults/activate", body: { ...PAYMENT, duration_seconds: 31 } },
  { name: "different canonical mutation path", path: "/faults/deactivate", body: { scenario: "payments_errors" } },
  { name: "noncanonical alias", path: "/faults/activate/", body: PAYMENT },
];
for (const other of interleaved) {
  test(`interleaved ${other.name} cannot steal the pending provenance stamp`, async ({ page, api, boundary }) => {
    mock(api, "POST", other.path, { status: 200, body: controllerStatus("manual") });
    await openUi(page);
    const token = await arm(page, boundary.gate);
    const checkProvenance = provenance(page, token);
    // Register AFTER the boundary init script's document-capture listener. This
    // synchronous fetch runs after it marks the trusted click pending and BEFORE
    // React's target/bubble handler sends the intended mutation. The wrapper must
    // match method + exact canonical path + deep-equal JSON body before stamping.
    await interleave(page, other.path, other.body);
    await inject(page).click();
    const result = await page.evaluate(() => (window as ProbeWindow).__t2Unrelated!);
    const results = await outcomes(page, 2);
    checkProvenance();
    blocked(result, api, boundary, 1);
    expect(results.filter((r) => r.outcome === 200)).toHaveLength(1);
    expect(writes(api)[0]).toMatchObject({ path: "/faults/activate", body: PAYMENT });
    consumed(boundary.gate);
  });
}
async function interleave(page: Page, path: string, body: unknown) {
  await page.evaluate(({ path, body }) => {
    document.addEventListener("click", (event) => {
      const button = event.target instanceof Element ? event.target.closest("button") : null;
      if (!event.isTrusted || button?.textContent?.trim() !== "Inject fault") return;
      (window as ProbeWindow).__t2Unrelated = fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
        .then((r) => r.status, () => "rejected" as const);
    }, { capture: true });
  }, { path, body });
}

test("identical interleaving within the click window permits exactly one POST and refuses the duplicate", async ({ page, api, boundary }) => {
  await openUi(page);
  const token = await arm(page, boundary.gate);
  const checkProvenance = provenance(page, token);
  // Inherent limitation: an unrelated IDENTICAL method/path/body in this window
  // is indistinguishable from the intended handler. Assert one-use isolation only;
  // this test cannot establish ownership or eliminate external controller races.
  await interleave(page, "/faults/activate", PAYMENT);
  await inject(page).click();
  const results = await outcomes(page, 2);
  checkProvenance();
  expect(results.map((r) => r.outcome).sort()).toEqual([200, "rejected"]);
  expect(writes(api)).toHaveLength(1);
  expect(boundary.gate.violations).toHaveLength(1);
  expect(boundary.gate.violations[0]).toMatch(/duplicate/i);
  expect(boundary.violations).toEqual(boundary.gate.violations);
});
