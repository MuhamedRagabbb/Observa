// T2 / P5-G Stage 2, Gate 2-A. Needs E2E_STACK_URL=http://127.0.0.1:18000,
// E2E_STACK_CONTROLLER_URL=http://127.0.0.1:18090, a preview project running the
// P5-G image (operator served at /), and one worker. Starts REAL preview faults:
// one 30 s F2 and one 0/5/5 s cycle per project. NEVER point this at the live stack.
// No local bundle: this verifies the image-served page. Only 1440 and 390 run.
// EXCLUSIVE ACCESS REQUIRED: reserve the preview for this run; no traffic generator,
// other operator or other session may use it. E2E_T2_EXCLUSIVE=1 is the human attestation.
// Context routes cover pages, popups, frames and dedicated workers. Shared/service
// workers and controller beacons are disabled; controller keepalive writes are refused.
// Provenance is test isolation:
// a stamp requires a trusted matching control click AND the exact expected request.
// An identical request inside that click window can still take the one stamp.
// Browser interception does not eliminate external controller races: idle GET -> start
// POST, stopCheck GET -> Stop POST, plan/stopCheck -> cleanup POST, replacement work
// in sampling gaps, a same-config replacement cycle, or allowed-name foreign activity.
// The controller has no conditional operations; exclusive access remains necessary.
import { request as http, type APIRequestContext, type Page, type Request, type Response, type Route, type TestInfo } from "@playwright/test";
import type { components } from "../src/api/generated/controller.ts";
import { groundTruthLeaks, shouldInspectJson } from "./support/ground-truth.ts";
import { sectionName } from "./support/region-names.ts";
import { allowedOrigin, t2PreviewTargets } from "./support/stack-safety.ts";
import { armUiAction, installMutationBoundary } from "./support/t2-boundary.ts";
import { MutationGate, OwnershipLedger, foreignActivity, healthMismatch, observedPhases, quietWindow, recoveringUiEvidence, recoveryDeadline, recoveryVerdict, type GateKind, type StatusSample } from "./support/t2-evidence.ts";
import { CONTROLLER_URL, expect, test as stackTest } from "./support/stack.ts";

type Status = components["schemas"]["ControllerStatus"];
type Health = components["schemas"]["HealthSnapshot"];
type Activity = components["schemas"]["Activity"];
type Audit = { health: Health | null; posts: string[] };
const SERVICES = ["api-gateway", "inventory-service", "orders-service", "payments-service"];
const PAYMENT = { scenario: "payments_errors", duration_seconds: 30, parameters: { failure_rate: 0.5, status_code: 503 } };
const CYCLE = { seed: 7341, scenarios: ["payments_errors"], shuffle: false, warmup_seconds: 0, duration_seconds: 5, recovery_seconds: 5, repeat: 1 };
const WAIT = { timeout: 15_000 };
const RECOVERY_LIMIT_SECONDS = 180;
// ended_at precedes journal fsync; the internal recovery clock starts after fsync;
// polling contributes further skew. Allow at most 5 s; the controller limit stays 180 s.
const RECOVERY_TOLERANCE_SECONDS = 5;
// Observation time never changes the controller's recovery limit or verdict.
const RECOVERY_OBSERVATION_ALLOWANCE_MS = 30_000;
const CLEANUP_BUDGET_MS = 250_000; // includes a lost-response manual TTL + recovery + observation
const STATUS_INTERVAL_MS = 250;
type BannerSnapshot = { at: number; kind: string | null; title: string; text: string; cycleAcknowledged: boolean };
type EvidenceWindow = Window & { __t2Banners?: BannerSnapshot[] };
const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// Local locators deliberately do not import the contract fixture or its harness.
const banner = (page: Page) => page.getByRole("region", { name: "Experiment", exact: true });
const title = (page: Page) => banner(page).getByRole("heading", { level: 1 });
const manual = (page: Page) => page.getByRole("region", { name: sectionName("Manual fault injection") });
const cyclePanel = (page: Page) => page.getByRole("region", { name: sectionName("Automatic cycle") });
const phoneBar = (page: Page) => page.getByRole("region", { name: "Run in progress", exact: true });
const activity = (page: Page) => page.getByRole("region", { name: "Session activity", exact: true });
const healthTable = (page: Page) => page.getByRole("table", { name: "Service health", exact: true });

type BoundaryOptions = Parameters<typeof installMutationBoundary>[1];
const test = stackTest.extend<{ installedBoundary: BoundaryOptions; audit: Audit; run: OwnedRun }>({
  installedBoundary: [async ({ context }, use, info) => {
    test.skip(info.project.name === "stack-mobile-375", "T2 runs only at 1440 and 390");
    previewGuard(info);
    const boundary: BoundaryOptions = {
      origin: CONTROLLER_URL, gate: new MutationGate(), requireNoPages: true,
      onViolation: (reason) => { throw new Error(`T2 mutation refused before ownership setup: ${reason}`); },
      beforeForward: () => "ownership run is not ready",
    };
    // The route reads these callbacks at request time; run binds ownership before use.
    await installMutationBoundary(context, boundary);
    await use(boundary);
  }, { auto: true }],
  page: async ({ context, installedBoundary }, use) => {
    void installedBoundary; // dependency guarantees installation before context.newPage()
    const page = await context.newPage();
    await use(page);
  },
  run: [async ({ page, installedBoundary, audit }, use, info) => {
    test.skip(info.project.name === "stack-mobile-375", "T2 runs only at 1440 and 390");
    void audit; // ownership teardown runs before audit closes the page / removes its guards
    previewGuard(info);
    const api = await newControllerApi();
    const run = new OwnedRun(api, page, audit, installedBoundary.gate);
    try {
      run.bindGate(installedBoundary);
      await use(run);
    } finally {
      // A fixture timeout is independent of the test body's timeout; no page is needed to clean up.
      try { await run.cleanup(); } finally { await api.dispose(); }
    }
  }, { auto: true, timeout: 270_000 }],
  audit: [async ({ page, context, offOrigin }, use, info) => {
    test.skip(info.project.name === "stack-mobile-375", "T2 runs only at 1440 and 390");
    const audit: Audit = { health: null, posts: [] };
    const violations: string[] = [];
    const pending = new Set<Promise<void>>();
    const inFlight = new Set<Request>();
    const requested = (request: Request) => {
      if (!allowedOrigin(request.url(), [CONTROLLER_URL])) return;
      inFlight.add(request);
      if (request.method() === "POST") audit.posts.push(new URL(request.url()).pathname);
    };
    const finished = (request: Request) => { inFlight.delete(request); };
    const collect = (response: Response) => {
      if (!allowedOrigin(response.url(), [CONTROLLER_URL]) || !shouldInspectJson(response.status(), response.headers()["content-type"])) return;
      const scan = response.json().then((body: unknown) => {
        violations.push(...groundTruthLeaks(body).map((path) => `${response.url()}: ${path}`));
        if (new URL(response.url()).pathname === "/services/health" && response.status() === 200) audit.health = body as Health;
      }).catch((error: unknown) => {
        violations.push(`JSON inspection failed for ${response.url()}: ${String(error)}`);
      });
      pending.add(scan);
      void scan.then(() => pending.delete(scan));
    };
    context.on("response", collect);
    context.on("request", requested);
    context.on("requestfinished", finished);
    context.on("requestfailed", finished);
    try {
      await use(audit);
    } finally {
      // Keep inspection installed through page closure, including on failure/crash.
      // An unreadable JSON body is a failure, never silently exempted from scanning.
      try {
        // Quiesce controller polling, let existing bodies finish, then close. Other origins
        // remain subject to the original offOrigin guard, including during teardown.
        await context.route((url) => url.origin === CONTROLLER_URL, (route) => route.abort("blockedbyclient"));
        await expect.poll(() => inFlight.size + pending.size, { timeout: 10_000 }).toBe(0);
        await page.close();
        await expect.poll(() => pending.size, { timeout: 10_000 }).toBe(0);
      } finally {
        context.off("response", collect);
        context.off("request", requested);
        context.off("requestfinished", finished);
        context.off("requestfailed", finished);
        expect(violations, "every controller JSON response must be inspectable and free of ground truth").toEqual([]);
        expect(offOrigin, "the operator must only contact the preview controller").toEqual([]);
      }
    }
  }, { auto: true }],
});

test.use({ allowedOrigins: [CONTROLLER_URL], serviceWorkers: "block", actionTimeout: 15_000, navigationTimeout: 15_000 });
test.describe.configure({ mode: "serial", retries: 0 });
test.skip(({ viewport }) => viewport?.width === 375, "Skip stack-mobile-375: T2 runs only at 1440 and 390.");

function previewGuard(info: TestInfo) {
  // Preview-only guard: no API context or state-changing request exists before it passes.
  const targets = t2PreviewTargets(process.env);
  if (targets.controller !== CONTROLLER_URL) {
    throw new Error("T2 requires the guarded controller origin to match CONTROLLER_URL");
  }
  if (info.config.workers !== 1 || info.project.retries !== 0 || info.project.repeatEach !== 1
      || !["stack-desktop-1440", "stack-mobile-390"].includes(info.project.name)) {
    throw new Error("T2 requires one worker, zero retries, repeatEach=1, and only stack-desktop-1440 / stack-mobile-390");
  }
}

const newControllerApi = () => http.newContext({ baseURL: CONTROLLER_URL, timeout: 10_000, maxRedirects: 0, extraHTTPHeaders: { "x-control-token": "local-control-only" } });
const idle = (status: StatusSample) => status.state === "idle" && status.active === null && !status.auto_running;

async function read<T>(api: APIRequestContext, path: string, violations: string[] = [], timeout = 2_000): Promise<T> {
  const response = await api.get(path, { maxRedirects: 0, timeout });
  expect(response.status(), `controller GET ${path}: ${await response.text()}`).toBe(200);
  const body: unknown = await response.json();
  const leaks = groundTruthLeaks(body);
  violations.push(...leaks.map((key) => `${path}: ${key}`));
  expect(leaks, `ground truth in controller GET ${path}`).toEqual([]);
  return body as T;
}

function requireIdle(status: Status, where: string) {
  if (!idle(status) || status.controls.activate?.enabled !== true) {
    throw new Error(`T2 ${where} found a non-idle preview (${status.state}); aborted with ZERO mutations`);
  }
}

test.beforeAll(async ({}, info) => {
  if (info.project.name === "stack-mobile-375") return;
  previewGuard(info);
  const api = await newControllerApi();
  try {
    const samples: Status[] = [];
    const heads: string[] = [];
    const started = Date.now();
    do {
      const status = await read<Status>(api, "/faults/status");
      requireIdle(status, "preflight");
      samples.push(status);
      heads.push(JSON.stringify((await read<Activity>(api, "/activity?limit=200")).entries[0] ?? null));
      if (Date.now() - started >= 4_000 && status.now - samples[0]!.now >= 4) break;
      if (Date.now() - started >= 10_000) break;
      await pause(1_000);
    } while (true);
    const quiet = quietWindow(samples, heads);
    if (!quiet.quiet) throw new Error(`T2 exclusive preview quiet window refused: ${quiet.reason}; ZERO mutations`);
  }
  finally { await api.dispose(); }
});

test.beforeEach(async ({ run }) => {
  // No begin here: a refused preflight/beforeEach leaves the ledger empty.
  requireIdle(await run.read<Status>("/faults/status"), "beforeEach");
});

class OwnedRun {
  readonly ledger = new OwnershipLedger();
  readonly samples: (Status & { hostAt: number })[] = [];
  private readonly violations: string[] = [];
  private readonly failures: unknown[] = [];
  private owned = false;
  private recording = false;
  private recorder: Promise<void> | undefined;
  private readonly responseListeners = new Set<(response: Response) => void>();
  private readonly responseTasks = new Set<Promise<void>>();
  private acceptConfirmation = true;
  private statusTail: Promise<unknown> = Promise.resolve();
  private observationOrder = 0;
  private confirmationOrder: number | null = null;
  private readonly requestIds = new WeakMap<Request, string>();
  private readonly requestSlots = new WeakMap<Request, number>();
  private armSequence = 0;
  private nextRequest = 0;
  private armedKind: GateKind | null = null;
  private activitySince: string | null = null;
  private allowedEvents: string[] = [];
  private foreignReason: string | null = null;
  private monitoring = false;
  private monitor: Promise<void> | undefined;
  private closing = false;

  constructor(private readonly api: APIRequestContext, private readonly page: Page, private readonly audit: Audit, readonly gate: MutationGate) {}

  read<T>(path: string, timeout = 2_000): Promise<T> {
    if (path !== "/faults/status") return read<T>(this.api, path, this.violations, timeout);
    const task = this.statusTail.then(async () => {
      const hostAt = Date.now(); // request START, not response delivery
      const requestOrder = ++this.observationOrder;
      const status = await read<Status>(this.api, path, this.violations, timeout);
      const sample = { ...status, hostAt };
      this.samples.push(sample);
      // Sequence ordering also distinguishes requests begun in the same host millisecond.
      if (this.confirmationOrder !== null && requestOrder > this.confirmationOrder) this.ledger.observe(sample);
      return sample as T;
    });
    this.statusTail = task.catch(() => undefined);
    return task;
  }

  bindGate(boundary: BoundaryOptions) {
    boundary.onViolation = (reason) => { this.failures.push(new Error(`T2 mutation refused: ${reason}`)); };
    boundary.beforeForward = () => {
      const continuity = this.armedKind?.startsWith("stop-") ? this.ledger.stopCheck(this.samples.at(-1) ?? null) : null;
      return this.closing ? "page mutation during cleanup"
        : this.foreignReason ?? this.ledger.revoked ?? (continuity && !continuity.allowed ? continuity.reason : null);
    };
    boundary.onAuthorized = ({ request, id }) => {
      this.requestIds.set(request, id);
      this.requestSlots.set(request, this.armSequence);
    };
  }

  async startRecording() {
    // Retain transient banner evidence without delaying the controller recorder or Stop click.
    await this.page.addInitScript(() => {
      const snapshots: BannerSnapshot[] = [];
      (window as EvidenceWindow).__t2Banners = snapshots;
      new MutationObserver(() => {
        const region = document.querySelector('[aria-label="Experiment"]');
        if (!region) return;
        const snapshot = {
          at: Date.now(),
          kind: region.getAttribute("data-kind"), title: region.querySelector("h1")?.textContent ?? "", text: region.textContent ?? "",
          cycleAcknowledged: document.querySelector('input[name="seed"]')?.closest("section")?.textContent?.includes("Controller confirmed the cycle is running.") ?? false,
        };
        const previous = snapshots.at(-1);
        if (!previous || previous.kind !== snapshot.kind || previous.title !== snapshot.title || previous.text !== snapshot.text
            || previous.cycleAcknowledged !== snapshot.cycleAcknowledged) snapshots.push(snapshot);
      }).observe(document, { subtree: true, childList: true, characterData: true, attributes: true });
    });
    this.recording = true;
    await this.capture();
    this.recorder = (async () => {
      while (this.recording) {
        await pause(STATUS_INTERVAL_MS);
        if (this.recording) await this.capture();
      }
    })();
    this.monitoring = true;
    this.monitor = (async () => {
      let mismatches = 0;
      while (this.monitoring) {
        try {
          if (this.audit.health) {
            const rows = await healthTable(this.page).getByRole("row").evaluateAll((elements) => elements.flatMap((row) => {
              const name = row.querySelector('th[scope="row"]');
              return name ? [{ service: name.textContent ?? "", label: row.querySelector("td > span")?.textContent ?? "" }] : [];
            }));
            const mismatch = healthMismatch(this.audit.health, rows, SERVICES);
            mismatches = mismatch ? mismatches + 1 : 0;
            if (mismatches === 3) this.failures.push(new Error(`T2 health mismatch for 3 consecutive observations: ${mismatch}`));
          }
          await this.checkActivity();
        } catch (error) { this.failures.push(error); }
        await pause(1_000);
      }
    })();
  }

  capture(timeout = 2_000) {
    return this.read<Status>("/faults/status", timeout).catch(() => null);
  }

  private async checkActivity() {
    if (this.activitySince === null) return;
    const entries = (await this.read<Activity>("/activity?limit=200")).entries;
    const findings = foreignActivity(entries, this.activitySince, this.allowedEvents);
    if (findings.length) {
      this.foreignReason ??= findings.join("; ");
      this.ledger.revoke(this.foreignReason);
      this.failures.push(new Error(this.foreignReason));
    }
    if (entries[0]) this.activitySince = entries[0].at;
  }

  async bannerEvidence() {
    return this.page.evaluate(() => (window as EvidenceWindow).__t2Banners ?? []);
  }

  async start(kind: "manual" | "cycle", click: () => Promise<void>) {
    this.activitySince = (await this.read<Activity>("/activity?limit=200")).entries[0]?.at ?? "";
    // Exact log.record names in controller app.py; manual work permits no phase events.
    this.allowedEvents = ["fault_requested", "fault_active", "stop_requested", "fault_stopped", "service_health_changed", "services_recovered"];
    if (kind === "cycle") this.allowedEvents.push("cycle_started", "phase_started", "cycle_finished", "cycle_stop_requested", "cycle_stopped");
    // The last operation before arming the single UI click is a fresh, read-only idle check.
    requireIdle(await this.read<Status>("/faults/status"), "immediately before start");
    this.ledger.begin(kind);
    await this.arm(kind === "manual" ? "start-manual" : "start-cycle", kind === "manual" ? PAYMENT : CYCLE, kind === "manual" ? "Inject fault" : "Start cycle");
    return this.clickAndConfirm(kind, true, click);
  }

  private async arm(kind: GateKind, body: unknown, name: string) {
    await armUiAction(this.page, this.gate, { kind, body, control: { role: "button", name } });
    this.armSequence += 1;
    this.armedKind = kind;
  }

  async stop(kind: "manual" | "cycle", click: () => Promise<void>, controlName = kind === "manual" ? "Stop fault" : "Stop cycle") {
    await this.checkActivity();
    const status = await this.capture();
    const check = this.ledger.stopCheck(status);
    if (!check.allowed || this.foreignReason) {
      const error = new Error(`T2 Stop denied: ${this.foreignReason ?? check.reason}`);
      this.failures.push(error);
      throw error; // DO NOT CLICK
    }
    // A fresh GET is NOT an atomic guard: another writer can act in the GET->POST gap.
    await this.arm(kind === "manual" ? "stop-manual" : "stop-cycle", kind === "manual" ? { scenario: "payments_errors" } : null, controlName);
    return this.clickAndConfirm(kind, false, click);
  }

  private async clickAndConfirm(kind: "manual" | "cycle", starting: boolean, click: () => Promise<void>) {
    const expectedKind = this.armedKind;
    const expectedSlot = this.armSequence;
    let resolveAnswer!: (answer: { response: Response; body: Status } | null) => void;
    const answer = new Promise<{ response: Response; body: Status } | null>((resolve) => { resolveAnswer = resolve; });
    let responseTask: Promise<void> | undefined;
    const listener = (response: Response) => {
      const id = this.requestIds.get(response.request());
      if (!id || !this.gate.isAuthorized(id) || this.requestSlots.get(response.request()) !== expectedSlot
          || this.armedKind !== expectedKind || responseTask) return;
      // Confirmation belongs to this listener, even if the click/body times out or the page crashes.
      responseTask = (async () => {
        try {
          if (response.status() !== 200) return;
          const body = await response.json() as Status;
          if (!this.acceptConfirmation) return;
          if (!starting) {
            if (!["recovering", "idle"].includes(body.state) || body.active !== null || body.auto_running !== false) return;
            this.finish(); // retire on the correlated stop proof before any test assertion
          } else if (kind === "manual") {
            if (typeof body.active?.incident_id !== "string" || !body.active.incident_id) return;
            this.ledger.confirmManual(body.active.incident_id);
          } else {
            if (body.auto_running !== true || !body.auto?.config || !Number.isFinite(body.auto.phase_started_at)) return;
            this.ledger.confirmCycle({ config: body.auto.config, startedAt: body.auto.phase_started_at });
          }
          if (starting) {
            this.confirmationOrder = ++this.observationOrder;
            this.owned = true;
            if (this.foreignReason) this.ledger.revoke(this.foreignReason);
          }
          resolveAnswer({ response, body });
        } catch { /* missing/unparsable bodies leave ownership unresolved */ }
        finally { resolveAnswer(null); this.page.off("response", listener); this.responseListeners.delete(listener); }
      })();
      this.responseTasks.add(responseTask);
      void responseTask.then(() => this.responseTasks.delete(responseTask!));
    };
    this.responseListeners.add(listener);
    this.page.on("response", listener); // installed BEFORE the only click
    const timer = setTimeout(() => resolveAnswer(null), WAIT.timeout);
    try {
      await click();
      const confirmed = await answer;
      if (!confirmed) throw new Error(this.ledger.plan(await this.capture()).report ?? "OWNERSHIP unproven; nothing was stopped");
      return confirmed;
    } finally { clearTimeout(timer); }
  }

  finish() { this.ledger.finish(); this.owned = false; }

  private async waitIdle(deadline: number) {
    while (Date.now() < deadline) {
      const status = await this.read<Status>("/faults/status", Math.max(1, Math.min(2_000, deadline - Date.now()))).catch(() => null);
      if (status && idle(status)) {
        expect(status).toMatchObject({ auto: null, controls: { activate: { enabled: true } } });
        return;
      }
      await pause(Math.max(1, Math.min(STATUS_INTERVAL_MS, deadline - Date.now())));
    }
    throw new Error("T2 read-only cleanup observation exhausted its bounded idle-wait budget");
  }

  async cleanup() {
    const deadline = Date.now() + CLEANUP_BUDGET_MS;
    this.closing = true;
    this.recording = false;
    this.monitoring = false;
    await this.recorder;
    await this.monitor;
    for (const listener of this.responseListeners) this.page.off("response", listener);
    // Drain an in-progress response body before planning; freeze confirmations if it stays unreadable.
    let drainTimer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([Promise.all(this.responseTasks), new Promise<void>((resolve) => { drainTimer = setTimeout(resolve, 10_000); })]);
    } finally { clearTimeout(drainTimer); this.acceptConfirmation = false; }
    // Independent Node observations still work after a page crash. Foreign activity denies cleanup.
    await this.checkActivity().catch((error: unknown) => {
      this.foreignReason ??= `activity continuity unreadable: ${String(error)}`;
      this.ledger.revoke(this.foreignReason);
      this.failures.push(error);
    });
    const status = await this.read<Status>("/faults/status").catch(() => null);
    const plan = this.ledger.plan(status);
    try {
      if (plan.report) {
        this.failures.push(new Error(plan.report));
        // An uncertain start may self-expire. Observe only; even a matching scenario proves nothing.
        if (this.ledger.unresolved) await this.waitIdle(deadline);
      } else {
        for (const action of plan.actions) {
          try {
            const check = this.ledger.stopCheck(status);
            if (!check.allowed || this.foreignReason) throw new Error(`T2 cleanup denied: ${this.foreignReason ?? check.reason}`);
            const kind = action === "deactivate-manual" ? "stop-manual" : "stop-cycle";
            const path = action === "deactivate-manual" ? "/faults/deactivate" : "/faults/auto/stop";
            const body = action === "deactivate-manual" ? { scenario: "payments_errors" } : null;
            const token = this.gate.arm(kind, body);
            const decision = this.gate.decide({ id: `node-${++this.nextRequest}`, method: "POST", path, body, token });
            if (!decision.allow) throw new Error(`T2 cleanup mutation refused: ${decision.reason}`);
            // plan()/stopCheck() -> POST is still a race without controller conditional operations.
            const response = action === "deactivate-manual"
              ? await this.api.post("/faults/deactivate", { data: { scenario: "payments_errors" }, maxRedirects: 0 })
              : await this.api.post("/faults/auto/stop", { maxRedirects: 0 });
            expect(response.status(), `T2 owned cleanup ${action}: ${await response.text()}`).toBe(200);
            const stopped = await response.json() as Status;
            if (["recovering", "idle"].includes(stopped.state) && stopped.active === null && !stopped.auto_running) this.finish();
          } catch (error) { this.failures.push(error); }
        }
        // Fresh/finished ledgers never wait for or stop another operator's work.
        if (status && (plan.actions.length > 0 || (this.owned && !status.active && !status.auto_running))) await this.waitIdle(deadline);
      }
    } catch (error) { this.failures.push(error); }
    finally { this.finish(); }
    if (this.samples.some((sample) => sample.state === "recovering")) {
      try { expect(recoveringUiEvidence(this.samples, await this.bannerEvidence()).verdict, "retained recovering UI evidence").not.toBe("missed"); }
      catch (error) { this.failures.push(error); }
    }
    if (this.ledger.revoked) this.failures.push(new Error(`OWNERSHIP revoked: ${this.ledger.revoked}; nothing further was stopped`));
    for (const refusal of this.gate.violations) this.failures.push(new Error(refusal));
    if (this.violations.length) this.failures.push(new Error(`Ground truth in controller GETs: ${this.violations.join(", ")}`));
    // Playwright reports fixture teardown errors alongside any original test error.
    if (this.failures.length) throw new AggregateError(this.failures, `T2 cleanup/evidence failed: ${this.failures.map(String).join("; ")}`);
  }
}

async function expandAll(page: Page) {
  for (const toggle of await page.locator("button[data-disclosure-toggle][aria-expanded='false']").all()) {
    const section = toggle.locator("xpath=ancestor::section[1]");
    if (await section.locator("input[readonly][aria-disabled='true']").count() === 0) await toggle.click();
  }
}

async function assertHealth(page: Page, audit: Audit) {
  // Compare to the actual snapshot delivered to this page, allowing its React render to catch up.
  // Never manufacture healthy/degraded states or require traffic on an otherwise idle preview.
  await expect.poll(async () => {
    const snapshot = audit.health;
    if (!snapshot) return false;
    expect(snapshot.services.map((row) => row.service).sort()).toEqual(SERVICES);
    const table = healthTable(page);
    if (await table.getByRole("row").count() !== 5) return false;
    const names = await table.getByRole("rowheader").allTextContents();
    if (JSON.stringify(names.sort()) !== JSON.stringify(SERVICES)) return false;
    for (const service of snapshot.services) {
      const row = table.getByRole("row").filter({ has: page.getByRole("rowheader", { name: service.service, exact: true }) });
      const label = service.state.charAt(0).toUpperCase() + service.state.slice(1);
      if (await row.getByText(label, { exact: true }).count() !== 1) return false;
      for (const other of ["Healthy", "Degraded", "Unavailable", "Unknown"].filter((value) => value !== label)) {
        if (await row.getByText(other, { exact: true }).count() !== 0) return false;
      }
    }
    return true;
  }, WAIT).toBe(true);
}

async function openOperator(page: Page, audit: Audit) {
  await page.goto(`${CONTROLLER_URL}/`);
  await expect(page).toHaveURL(`${CONTROLLER_URL}/`);
  await expect(title(page)).toHaveText("Idle");
  await expect(page.getByRole("banner").getByText("Controller connected", { exact: true })).toBeVisible();
  await assertHealth(page, audit);
  await expandAll(page);
}

async function recoverToIdle(page: Page, run: OwnedRun, audit: Audit) {
  // Host time bounds observation only. The verdict uses each controller sample's own clock.
  let safetyDeadline = Date.now() + (RECOVERY_LIMIT_SECONDS + RECOVERY_TOLERANCE_SECONDS) * 1_000 + RECOVERY_OBSERVATION_ALLOWANCE_MS;
  let anchored = false;
  for (;;) {
    if (Date.now() >= safetyDeadline) throw new Error("T2 controller samples stayed pending beyond the bounded recovery observation allowance");
    const captured = await run.capture(Math.max(1, Math.min(2_000, safetyDeadline - Date.now())));
    const samples = [...run.samples];
    const sample = captured ? samples.at(-1) : null;
    const result = recoveryVerdict(samples, { limitSeconds: RECOVERY_LIMIT_SECONDS, toleranceSeconds: RECOVERY_TOLERANCE_SECONDS });
    expect(result.verdict, `controller still recovering beyond its fault-end deadline (${result.elapsedSeconds}s)`).not.toBe("overran");
    if (sample?.last_fault && !anchored) {
      const remaining = Math.max(0, recoveryDeadline(sample.last_fault, RECOVERY_LIMIT_SECONDS) + RECOVERY_TOLERANCE_SECONDS - sample.now);
      safetyDeadline = Math.min(safetyDeadline, Date.now() + remaining * 1_000 + RECOVERY_OBSERVATION_ALLOWANCE_MS);
      anchored = true;
    }
    if (sample && result.verdict === "idle") {
      expect(sample).toMatchObject({ state: "idle", active: null, auto_running: false, auto: null, last_fault: { ended_at: expect.any(String) }, controls: { activate: { enabled: true } } });
      expect(Number.isFinite(Date.parse(sample.last_fault!.ended_at))).toBe(true);
      run.finish(); // stop was confirmed, or the cycle_finished event was already observed
      break;
    }
    if (Date.now() >= safetyDeadline) throw new Error("T2 controller samples stayed pending beyond the bounded recovery observation allowance");
    await pause(Math.min(STATUS_INTERVAL_MS, safetyDeadline - Date.now()));
  }
  // A late first idle sample is valid; UI convergence retains its separate 15 s WAIT.
  await expect(title(page)).toHaveText("Idle", WAIT);
  expect(recoveringUiEvidence(run.samples, await run.bannerEvidence()).verdict, "retained recovering UI evidence").not.toBe("missed");
  await assertHealth(page, audit);
}

test("read-only preflight: image-served root, legacy redirect, idle controls and real health rows", async ({ page, run, audit, offOrigin }) => {
  expect(await run.read<Status>("/faults/status")).toMatchObject({ state: "idle", active: null, controls: { activate: { enabled: true } } });
  await openOperator(page, audit);
  const navigation = await page.goto(`${CONTROLLER_URL}/ui/next/`);
  expect(navigation?.status()).toBe(200);
  const redirect = navigation?.request().redirectedFrom();
  expect(redirect?.url()).toBe(`${CONTROLLER_URL}/ui/next/`);
  expect((await redirect?.response())?.status()).toBe(308);
  await expect(page).toHaveURL(`${CONTROLLER_URL}/`);
  await expect(title(page)).toHaveText("Idle");
  await assertHealth(page, audit);
  expect(audit.posts).toEqual([]);
  expect(offOrigin).toEqual([]);
});

test("one real F2: UI ack, countdown/activity, phone-bar or banner Stop, Recovering -> Idle", async ({ page, run, audit, offOrigin }, info) => {
  await run.startRecording();
  await openOperator(page, audit);
  await manual(page).getByRole("radio", { name: "Payment Errors", exact: true }).check();
  await manual(page).getByRole("textbox", { name: "Failure rate", exact: true }).fill("50");
  await manual(page).getByRole("textbox", { name: "Duration", exact: true }).fill("30");
  await manual(page).getByRole("radiogroup", { name: "HTTP status", exact: true }).getByRole("radio", { name: "503 Service Unavailable", exact: true }).check();
  const since = (await run.read<Activity>("/activity?limit=200")).entries[0]?.at ?? "";
  const { response, body: active } = await run.start("manual", () => manual(page).getByRole("button", { name: "Inject fault", exact: true }).click()); // exactly once
  expect(response.request().postDataJSON()).toEqual(PAYMENT);
  expect(response.status()).toBe(200);
  expect(active).toMatchObject({ state: "manual", active: { scenario: "payments_errors", status: "active", parameters: PAYMENT.parameters, acknowledged_at: expect.any(String) } });
  await expect(manual(page)).toContainText("Controller confirmed the fault is active.");
  await expect(title(page)).toHaveText("Payment Errors");
  const clock = banner(page).getByText(/^\d{2}:\d{2}$/);
  await expect(clock).toBeVisible();
  // The displayed timer follows the controller's clock, not a locally invented duration.
  const [minutes, seconds] = (await clock.innerText()).split(":").map(Number);
  const shown = (minutes ?? 0) * 60 + (seconds ?? 0);
  expect(shown).toBeGreaterThan(0);
  expect(shown).toBeLessThanOrEqual(Math.ceil((active.active?.expires_at ?? 0) - active.now));
  await expect.poll(async () => {
    const [m, s] = (await clock.innerText()).split(":").map(Number);
    return (m ?? 0) * 60 + (s ?? 0);
  }, { timeout: 4_000 }).toBeLessThan(shown);
  await expect(activity(page).getByText(/^Fault requested.*F2 Payment Errors/)).toHaveCount(1);
  await expect(activity(page).getByText("Controller confirmed fault is active", { exact: true })).toHaveCount(1);
  await assertHealth(page, audit);
  const stop = info.project.name === "stack-mobile-390"
    ? phoneBar(page).getByRole("button", { name: "Stop Payment Errors", exact: true })
    : banner(page).getByRole("button", { name: "Stop fault", exact: true });
  if (info.project.name === "stack-mobile-390") {
    // Prove the sticky surface remains visible after scrolling away from the banner.
    await activity(page).scrollIntoViewIfNeeded();
    await expect(phoneBar(page)).toBeInViewport();
  }
  await expect(stop).toBeVisible();
  const stopped = await run.stop("manual", () => stop.click(), info.project.name === "stack-mobile-390" ? "Stop Payment Errors" : "Stop fault");
  expect(stopped.response.status()).toBe(200);
  expect(["recovering", "idle"]).toContain(stopped.body.state);
  expect(stopped.body).toMatchObject({ active: null, auto_running: false });
  await expect(page.getByText("Controller confirmed the fault stopped.", { exact: true })).toBeVisible();
  await recoverToIdle(page, run, audit);
  const phases = observedPhases(run.samples);
  expect(phases).toContain("manual");
  expect(phases.at(-1)).toBe("idle");
  const events = (await run.read<Activity>("/activity?limit=200")).entries.filter((entry) => entry.at > since);
  for (const event of ["fault_requested", "fault_active", "stop_requested", "fault_stopped"]) expect(events.filter((entry) => entry.event === event && entry.scenario === "payments_errors")).toHaveLength(1);
  expect(audit.posts).toEqual(["/faults/activate", "/faults/deactivate"]);
  expect(offOrigin).toEqual([]);
});

test("one short UI cycle: real config echo and 1-based step, completion at 390 / Stop cycle at 1440", async ({ page, run, audit, offOrigin }, info) => {
  await run.startRecording();
  await openOperator(page, audit);
  for (const [name, value] of [["Warmup", "0"], ["Fault duration", "5"], ["Recovery", "5"], ["Repeats", "1"], ["Seed", "7341"]] as const) {
    await cyclePanel(page).getByRole("textbox", { name, exact: true }).fill(value);
  }
  for (const [name, selected] of [["Database Exhaustion", false], ["Payment Errors", true], ["Bad Deployment", false]] as const) {
    await cyclePanel(page).getByRole("checkbox", { name, exact: true }).setChecked(selected);
  }
  const shuffle = cyclePanel(page).getByRole("switch", { name: "Shuffle scenarios", exact: true });
  if (await shuffle.getAttribute("aria-checked") === "true") await shuffle.click();
  await expect(shuffle).toHaveAttribute("aria-checked", "false");
  const since = (await run.read<Activity>("/activity?limit=200")).entries[0]?.at ?? "";
  const { response, body: started } = await run.start("cycle", () => cyclePanel(page).getByRole("button", { name: "Start cycle", exact: true }).click()); // exactly once
  const desktop = info.project.name === "stack-desktop-1440";
  // Nothing slow sits between observing the fault phase and the desktop Stop click.
  await expect.poll(() => run.samples.some((sample) => sample.auto?.phase === "fault"), { ...WAIT, intervals: [100] }).toBe(true);
  if (desktop) {
    const stopped = await run.stop("cycle", () => banner(page).getByRole("button", { name: "Stop cycle", exact: true }).click());
    expect(stopped.response.status()).toBe(200);
    expect(["recovering", "idle"]).toContain(stopped.body.state);
    expect(stopped.body).toMatchObject({ active: null, auto_running: false });
    await expect(page.getByText("Controller confirmed the cycle stopped.", { exact: true })).toBeVisible();
  } else {
    await expect.poll(async () => (await run.read<Activity>("/activity?limit=200")).entries.filter((entry) => entry.at > since && entry.event === "cycle_finished").length, { timeout: 20_000, intervals: [250] }).toBe(1);
    run.finish(); // explicit completion proof, before fallible retained-evidence assertions
  }
  // Heavy assertions use retained evidence, rather than consuming the five-second fault window.
  expect(response.request().postDataJSON()).toEqual(CYCLE);
  expect(response.status()).toBe(200);
  expect(started).toMatchObject({ state: "automatic", auto_running: true, auto: { repeat: 1, repeats: 1, steps: 1 } });
  expect(started.auto?.config).toEqual(CYCLE);
  expect(run.samples.find((sample) => sample.auto?.phase === "fault")?.auto).toMatchObject({ phase: "fault", scenario: "payments_errors", step: 1, steps: 1, repeat: 1, config: CYCLE });
  const views = await run.bannerEvidence();
  expect(views.some((view) => view.cycleAcknowledged), "recorded UI acknowledgement that the cycle is running").toBe(true);
  expect(views.some((view) => view.title === "Payment Errors" && view.text.includes("Fault phase") && view.text.includes("Repeat 1 of 1")), "recorded fault banner retains its title, phase and repeat").toBe(true);
  if (!desktop) {
    expect(run.samples.find((sample) => sample.auto?.phase === "recovery")?.auto).toMatchObject({ phase: "recovery", step: 1, steps: 1, repeat: 1, config: CYCLE });
    expect(views.some((view) => view.title === "Recovery" && view.text.includes("Recovery after Payment Errors")), "recorded automatic recovery banner").toBe(true);
  }
  await recoverToIdle(page, run, audit);
  const phases = observedPhases(run.samples);
  expect(phases).toContain("automatic:fault");
  if (!desktop) expect(phases.indexOf("automatic:recovery")).toBeGreaterThan(phases.indexOf("automatic:fault"));
  expect(phases.at(-1)).toBe("idle");
  const events = (await run.read<Activity>("/activity?limit=200")).entries.filter((entry) => entry.at > since);
  expect(events.filter((entry) => entry.event === "cycle_started")).toHaveLength(1);
  expect(events.filter((entry) => entry.event === "fault_requested")).toHaveLength(1);
  expect(events.filter((entry) => entry.event === "fault_stopped" && entry.scenario === "payments_errors")).toHaveLength(1);
  expect(events.filter((entry) => entry.event === "cycle_finished")).toHaveLength(desktop ? 0 : 1);
  expect(events.filter((entry) => entry.event === "cycle_stop_requested")).toHaveLength(desktop ? 1 : 0);
  expect(audit.posts).toEqual(desktop ? ["/faults/auto/start", "/faults/auto/stop"] : ["/faults/auto/start"]);
  expect(offOrigin).toEqual([]);
});

test("status route abort shows Disconnected without changing the runtime, then reconnects", async ({ page, run, audit, offOrigin }) => {
  await openOperator(page, audit);
  const before = await run.read<Status>("/faults/status");
  const since = (await run.read<Activity>("/activity?limit=200")).entries[0]?.at ?? "";
  const url = `${CONTROLLER_URL}/faults/status`;
  let aborted = 0;
  const abort = (route: Route) => {
    if (route.request().method() !== "GET") return route.fallback();
    aborted += 1;
    return route.abort("failed");
  };
  await page.route(url, abort);
  try {
    await expect(page.getByRole("banner").getByText("Controller disconnected", { exact: true })).toBeVisible();
    await expect(banner(page)).toHaveAttribute("data-kind", "disconnected");
    await expect(title(page)).toHaveText("Unreachable");
    await expect(banner(page).getByRole("button", { name: "Retry connection", exact: true })).toBeVisible();
    expect(aborted).toBeGreaterThan(0);
    const during = await run.read<Status>("/faults/status"); // Node read bypasses the page abort
    for (const key of ["state", "active", "auto", "auto_running", "last_fault", "controls"] as const) expect(during[key]).toEqual(before[key]);
  } finally {
    await page.unroute(url, abort);
  }
  // Let normal polling reconnect; avoids racing an auto-reconnect with a Retry click.
  await expect(page.getByRole("banner").getByText("Controller connected", { exact: true })).toBeVisible();
  await expect(title(page)).toHaveText("Idle");
  await assertHealth(page, audit);
  const after = await run.read<Status>("/faults/status");
  for (const key of ["state", "active", "auto", "auto_running", "last_fault", "controls"] as const) expect(after[key]).toEqual(before[key]);
  const events = (await run.read<Activity>("/activity?limit=200")).entries.filter((entry) => entry.at > since);
  expect(events.filter((entry) => /^(fault_|stop_|cycle_|phase_started|control_rejected)/.test(entry.event))).toEqual([]);
  expect(audit.posts).toEqual([]);
  expect(offOrigin).toEqual([]);
});
