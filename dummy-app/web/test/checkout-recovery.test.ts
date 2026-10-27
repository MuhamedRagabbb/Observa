// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/checkout"}
// P4-E recovery through the checkout flow (src/store/checkout/flow.ts), with a real IndexedDB
// gate (fake-indexeddb) and a scripted Gateway:
// - Check status: GET first; only 404 attempt_not_found leads to one resolve (U3, U3b, U5);
// - polling a processing attempt: GET only, 1, 2, 4, 8, 8 ... s for about 60 s (U4);
// - Check again after polling: GET first, then one resolve if it is still processing (D2);
// - a confirmed order's cart effect happens exactly once, across tabs and after a crash (D4);
// - R4: a confirmed result is re-read, read-only, by reference.
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CART_STORAGE_KEY, createCartStore } from "../src/store/cart/cart-store.ts";
import { ATTEMPTS_KEY, beginAttempt, createAttemptStore, CUSTOMER_KEY, type AttemptRecord } from "../src/store/checkout/attempts.ts";
import { createCheckoutFlow, POLL_BUDGET_MS } from "../src/store/checkout/flow.ts";
import { gateEffects, gateList, openGate, resetGate } from "../src/store/checkout/gate.ts";
import type { CheckoutResult } from "../src/store/checkout/outcome.ts";

const CUSTOMER = "web-0123456789abcdefghijkl";
const ORDERED = [
  { product_id: "sku-001", quantity: 1 },
  { product_id: "sku-002", quantity: 2 },
];
const contact = { name: "Demo Person", email: "demo.person@example.com" };

function result(attempt_ref: string, outcome: CheckoutResult["outcome"], extra: Partial<CheckoutResult> = {}): CheckoutResult {
  return {
    attempt_ref,
    order_id: outcome === "confirmed" ? "order-0f8fad5b-d9cb-469f-a165-70867728950e" : null,
    order_number: outcome === "confirmed" ? "OBS-1042-7F3A" : null,
    state: outcome === "processing" ? "charging" : "x",
    outcome,
    retryable: outcome !== "confirmed",
    items: ORDERED.map((line) => ({ ...line, unit_price_cents: 1200 })),
    total_cents: 3600,
    error_code: null,
    unavailable_items: null,
    created_at: "2026-10-27T10:00:00Z",
    ...extra,
  };
}

const json = (status: number, body: unknown) => Response.json(body, { status });
const envelope = (code: string, details?: CheckoutResult) => ({ error_code: code, message: "x", ...(details ? { details } : {}) });

type Call = { method: string; path: string; query: Record<string, string>; body: string; key: string };
const refOf = (call: Call) => call.path.split("/")[3] ?? "";
type Route = (call: Call) => Response | Promise<Response>;

/** A scripted Gateway: every request is recorded; `routes` answers by "METHOD kind". */
function gateway(routes: Partial<Record<"GET" | "RESOLVE" | "POST", Route | Route[]>>) {
  const calls: Call[] = [];
  const queues = new Map(Object.entries(routes).map(([k, v]) => [k, Array.isArray(v) ? [...v] : v]));
  const fetch = async (request: Request) => {
    const url = new URL(request.url);
    const call: Call = {
      method: request.method,
      path: url.pathname,
      query: Object.fromEntries(url.searchParams),
      body: await request.clone().text(),
      key: request.headers.get("Idempotency-Key") ?? "",
    };
    calls.push(call);
    const kind = request.method === "GET" ? "GET" : url.pathname.endsWith("/resolve") ? "RESOLVE" : "POST";
    const route = queues.get(kind);
    const handler = Array.isArray(route) ? (route.length > 1 ? route.shift() : route[0]) : route;
    if (!handler) throw new Error(`unexpected ${kind} ${url.pathname}`);
    return handler(call);
  };
  const count = (kind: "GET" | "RESOLVE" | "POST") =>
    calls.filter((c) => (kind === "GET" ? c.method === "GET" : kind === "RESOLVE" ? c.path.endsWith("/resolve") : c.method === "POST" && !c.path.endsWith("/resolve"))).length;
  return { fetch, calls, count };
}

const NETWORK: Route = () => Promise.reject(new TypeError("Failed to fetch"));

/** A sleep the test controls: records each delay and resolves at once (or never, once stopped). */
function sleeps() {
  const delays: number[] = [];
  const sleep = (ms: number, signal: AbortSignal) =>
    new Promise<void>((resolve, reject) => {
      if (signal.aborted) return reject(new DOMException("stopped", "AbortError"));
      delays.push(ms);
      resolve();
    });
  return { delays, sleep };
}

function tab(fetch: (request: Request) => Promise<Response>, sleep = sleeps().sleep) {
  const cart = createCartStore();
  const attempts = createAttemptStore();
  const flow = createCheckoutFlow({ cart, attempts, fetch, sleep, now: () => "2026-10-27T10:05:00.000Z" });
  return { cart, attempts, flow };
}

/** An attempt that was sent and came back unknown: stored in the gate, cart still holds it. */
async function unknownAttempt(status: AttemptRecord["status"] = "unknown"): Promise<AttemptRecord> {
  localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ORDERED }));
  const started = await beginAttempt(ORDERED, { customerId: CUSTOMER, now: () => "2026-10-27T10:00:00.000Z" });
  if (!started.ok) throw new Error("could not start");
  const store = createAttemptStore();
  if (status !== "submitting") await store.mark(started.attemptRef, status);
  await store.sync();
  const record = store.unconfirmed()[0];
  if (!record) throw new Error("not stored");
  return record;
}

async function held() {
  const db = await openGate();
  return db ? ((await gateList(db)) ?? []) : [];
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  resetGate();
  localStorage.clear();
  localStorage.setItem(CUSTOMER_KEY, CUSTOMER);
});
afterEach(() => resetGate());

describe("Check status", () => {
  it("unknown → GET finds the order confirmed: shown, settled, cart cleared, no resolve (U2)", async () => {
    const record = await unknownAttempt();
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "confirmed")) });
    const { flow, cart } = tab(api.fetch);
    const checked = await flow.check(record);
    expect(checked).toMatchObject({ kind: "definite", result: { outcome: "confirmed", order_number: "OBS-1042-7F3A" } });
    expect(flow.getSession()).toMatchObject({ phase: "done", attemptRef: record.attempt_ref, classification: { kind: "definite" } });
    expect(await held()).toEqual([]);
    expect(cart.getLines()).toEqual([]);
    expect(api.count("RESOLVE")).toBe(0);
    expect(api.count("POST")).toBe(0);
  });

  it("asks only by reference and the customer ID the attempt was sent with: no body, no contact", async () => {
    const record = await unknownAttempt();
    localStorage.setItem(CUSTOMER_KEY, "web-zzzzzzzzzzzzzzzzzzzzzz"); // the browser's ID changed since
    const api = gateway({ GET: () => json(404, envelope("attempt_not_found")), RESOLVE: () => json(409, envelope("attempt_abandoned", result(record.attempt_ref, "abandoned"))) });
    await tab(api.fetch).flow.check(record);
    expect(api.calls.map(({ method, path, query, body }) => ({ method, path, query, body }))).toEqual([
      { method: "GET", path: `/api/attempts/${record.attempt_ref}`, query: { customer_id: CUSTOMER }, body: "" },
      { method: "POST", path: `/api/attempts/${record.attempt_ref}/resolve`, query: { customer_id: CUSTOMER }, body: "" },
    ]);
  });

  it("404 → one resolve → abandoned: not placed, settled, the cart kept (U3)", async () => {
    const record = await unknownAttempt();
    const fenced = result(record.attempt_ref, "abandoned", { items: [], total_cents: null });
    const api = gateway({ GET: () => json(404, envelope("attempt_not_found")), RESOLVE: () => json(409, envelope("attempt_abandoned", fenced)) });
    const { flow, cart } = tab(api.fetch);
    expect(await flow.check(record)).toEqual({ kind: "definite", result: fenced });
    expect(api.count("RESOLVE")).toBe(1);
    expect(await held()).toEqual([]);
    expect(cart.getLines()).toEqual(ORDERED);
  });

  it.each([
    ["confirmed (201): recorded between the GET and the resolve", 201, "confirmed", []],
    ["payment declined (402)", 402, "payment_failed", ORDERED],
  ] as const)("404 → resolve may instead answer %s (U3b)", async (_name, status, outcome, cartAfter) => {
    const record = await unknownAttempt();
    const answer = result(record.attempt_ref, outcome, outcome === "payment_failed" ? { error_code: "payment_declined" } : {});
    const api = gateway({
      GET: () => json(404, envelope("attempt_not_found")),
      RESOLVE: () => json(status, status < 400 ? answer : envelope("payment_declined", answer)),
    });
    const { flow, cart } = tab(api.fetch);
    expect(await flow.check(record)).toMatchObject({ kind: "definite", result: { outcome } });
    expect(await held()).toEqual([]);
    expect(cart.getLines()).toEqual(cartAfter);
  });

  it("404 → resolve answers processing (202): the attempt stays, marked processing (U3b)", async () => {
    const record = await unknownAttempt();
    const api = gateway({ GET: () => json(404, envelope("attempt_not_found")), RESOLVE: () => json(202, result(record.attempt_ref, "processing")) });
    const { flow } = tab(api.fetch);
    expect(await flow.check(record)).toMatchObject({ kind: "definite", result: { outcome: "processing" } });
    expect((await held()).map((a) => a.status)).toEqual(["processing"]);
  });

  it.each([
    ["a network failure", NETWORK],
    ["422", () => json(422, envelope("invalid_attempt_key"))],
    ["502", () => json(502, envelope("dependency_unavailable"))],
    ["a malformed 200", () => json(200, { attempt_ref: "nope" })],
    ["a 404 that is not attempt_not_found", () => json(404, envelope("http_error"))],
  ])("a GET that fails (%s) stays unknown and never resolves (U5)", async (_name, answer) => {
    const record = await unknownAttempt();
    const api = gateway({ GET: answer });
    const { flow, cart } = tab(api.fetch);
    expect(await flow.check(record)).toMatchObject({ kind: "unknown" });
    expect(flow.getSession()).toMatchObject({ phase: "done", notice: "check_failed" });
    expect(api.count("RESOLVE")).toBe(0);
    expect((await held()).map((a) => [a.attempt_ref, a.status])).toEqual([[record.attempt_ref, "unknown"]]);
    expect(cart.getLines()).toEqual(ORDERED);
  });

  it.each([
    ["a network failure", NETWORK],
    ["409 attempt_abandoned without a result", () => json(409, envelope("attempt_abandoned"))],
    ["502 (a dependency failed while settling)", () => json(502, envelope("dependency_unavailable"))],
    ["a result for another reference", () => json(409, envelope("attempt_abandoned", result("ATT-FFFF-0001", "abandoned")))],
  ])("a resolve that fails (%s) keeps the attempt unknown", async (_name, answer) => {
    const record = await unknownAttempt();
    const api = gateway({ GET: () => json(404, envelope("attempt_not_found")), RESOLVE: answer });
    const { flow } = tab(api.fetch);
    expect(await flow.check(record)).toMatchObject({ kind: "unknown" });
    expect((await held()).map((a) => a.status)).toEqual(["unknown"]);
  });

  it("a second press while checking sends nothing more", async () => {
    const record = await unknownAttempt();
    let answer: (response: Response) => void = () => undefined;
    const api = gateway({ GET: () => new Promise<Response>((resolve) => (answer = resolve)) });
    const { flow } = tab(api.fetch);
    const first = flow.check(record);
    expect(await flow.check(record)).toEqual({ kind: "busy" });
    await Promise.resolve();
    answer(json(200, result(record.attempt_ref, "confirmed")));
    await first;
    expect(api.count("GET")).toBe(1);
  });

  it("a reload while 'submitting' (the answer was lost with the page) is checkable like unknown", async () => {
    const record = await unknownAttempt("submitting");
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "payment_failed")) });
    expect(await tab(api.fetch).flow.check(record)).toMatchObject({ kind: "definite", result: { outcome: "payment_failed" } });
    expect(await held()).toEqual([]);
  });

  it("a late confirmation takes out only the ordered units: units added meanwhile stay", async () => {
    const record = await unknownAttempt();
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "confirmed")) });
    const { flow, cart } = tab(api.fetch);
    cart.add("sku-002");
    cart.add("sku-003");
    await flow.check(record);
    expect(cart.getLines()).toEqual([
      { product_id: "sku-002", quantity: 1 },
      { product_id: "sku-003", quantity: 1 },
    ]);
  });
});

describe("two tabs check the same attempt (T5)", () => {
  it("both end confirmed and the cart loses the ordered units once", async () => {
    const record = await unknownAttempt();
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "confirmed")) });
    const a = tab(api.fetch);
    const b = tab(api.fetch);
    a.cart.add("sku-001"); // one more unit, added while the outcome was unknown
    const [first, second] = await Promise.all([a.flow.check(record), b.flow.check(record)]);
    expect(first).toMatchObject({ kind: "definite", result: { outcome: "confirmed" } });
    expect(second).toMatchObject({ kind: "definite", result: { outcome: "confirmed" } });
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual([{ product_id: "sku-001", quantity: 1 }]);
  });

  it("404 in both: both resolve (idempotent) and both end not placed", async () => {
    const record = await unknownAttempt();
    const fenced = result(record.attempt_ref, "abandoned", { items: [], total_cents: null });
    const api = gateway({ GET: () => json(404, envelope("attempt_not_found")), RESOLVE: () => json(409, envelope("attempt_abandoned", fenced)) });
    const results = await Promise.all([tab(api.fetch).flow.check(record), tab(api.fetch).flow.check(record)]);
    expect(results).toEqual([
      { kind: "definite", result: fenced },
      { kind: "definite", result: fenced },
    ]);
    expect(await held()).toEqual([]);
  });
});

describe("polling a processing attempt (U4, R3)", () => {
  it("polls with GET only, backing off 1, 2, 4, 8 s, until it completes", async () => {
    const record = await unknownAttempt("processing");
    const api = gateway({
      GET: [() => json(200, result(record.attempt_ref, "processing")), () => json(200, result(record.attempt_ref, "processing")), () => json(200, result(record.attempt_ref, "confirmed"))],
    });
    const timer = sleeps();
    const { flow, cart } = tab(api.fetch, timer.sleep);
    expect(await flow.follow(record).done).toBe("definite");
    expect(timer.delays).toEqual([1000, 2000, 4000]);
    expect(api.count("GET")).toBe(3);
    expect(api.count("RESOLVE") + api.count("POST")).toBe(0);
    expect(flow.getSession()).toMatchObject({ phase: "done", classification: { result: { outcome: "confirmed" } } });
    expect(cart.getLines()).toEqual([]);
  });

  it("gives up after about 60 s: still processing, nothing resolved, the attempt kept", async () => {
    const record = await unknownAttempt("processing");
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "processing")) });
    const timer = sleeps();
    const { flow } = tab(api.fetch, timer.sleep);
    expect(await flow.follow(record).done).toBe("still_processing");
    expect(timer.delays).toEqual([1000, 2000, 4000, 8000, 8000, 8000, 8000, 8000, 8000, 8000]);
    const waited = timer.delays.reduce((a, b) => a + b, 0);
    expect(waited).toBeGreaterThanOrEqual(POLL_BUDGET_MS);
    expect(waited).toBeLessThan(POLL_BUDGET_MS + 8000);
    expect(api.count("RESOLVE")).toBe(0);
    expect(flow.getSession()).toMatchObject({ phase: "done", notice: "still_processing" });
    expect((await held()).map((a) => a.status)).toEqual(["processing"]);
  });

  it("a failed poll is not an answer: it keeps polling and the attempt stays processing", async () => {
    const record = await unknownAttempt("processing");
    const api = gateway({ GET: [NETWORK, () => json(502, envelope("dependency_unavailable")), () => json(200, result(record.attempt_ref, "payment_failed"))] });
    const { flow } = tab(api.fetch);
    expect(await flow.follow(record).done).toBe("definite");
    expect(api.count("GET")).toBe(3);
  });

  it("a 404 while processing is inconsistent: unknown, and never resolved automatically", async () => {
    const record = await unknownAttempt("processing");
    const api = gateway({ GET: () => json(404, envelope("attempt_not_found")) });
    const { flow } = tab(api.fetch);
    expect(await flow.follow(record).done).toBe("unknown");
    expect(api.count("RESOLVE")).toBe(0);
    expect((await held()).map((a) => a.status)).toEqual(["unknown"]);
  });

  it("stop, then follow again at once (React StrictMode remount): it still polls", async () => {
    const record = await unknownAttempt("processing");
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "confirmed")) });
    const { flow } = tab(api.fetch);
    flow.follow(record).stop();
    expect(await flow.follow(record).done).toBe("definite");
    expect(api.count("GET")).toBe(1);
  });

  it("stopping (leaving the page) ends polling: nothing more is sent", async () => {
    const record = await unknownAttempt("processing");
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "processing")) });
    const { flow } = tab(api.fetch);
    const following = flow.follow(record);
    following.stop();
    expect(await following.done).toBe("stopped");
    const sent = api.count("GET");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(api.count("GET")).toBe(sent);
    expect(sent).toBeLessThanOrEqual(1);
  });

  it("Check again after polling: GET first, then one resolve while it is still processing (D2)", async () => {
    const record = await unknownAttempt("processing");
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "processing")), RESOLVE: () => json(201, result(record.attempt_ref, "confirmed")) });
    const { flow, cart } = tab(api.fetch);
    await flow.follow(record).done;
    expect(api.count("RESOLVE")).toBe(0);
    const gets = api.count("GET");
    expect(await flow.check(record)).toMatchObject({ kind: "definite", result: { outcome: "confirmed" } });
    expect(api.calls.slice(-2).map((c) => c.method + " " + c.path)).toEqual([
      `GET /api/attempts/${record.attempt_ref}`,
      `POST /api/attempts/${record.attempt_ref}/resolve`,
    ]);
    expect(api.count("GET")).toBe(gets + 1);
    expect(cart.getLines()).toEqual([]);
  });

  it("Check status on a processing attempt before polling gave up does not resolve: it polls", async () => {
    const record = await unknownAttempt("unknown");
    const api = gateway({ GET: () => json(200, result(record.attempt_ref, "processing")) });
    const { flow } = tab(api.fetch);
    expect(await flow.check(record)).toMatchObject({ kind: "definite", result: { outcome: "processing" } });
    expect(api.count("RESOLVE")).toBe(0);
    expect((await held()).map((a) => a.status)).toEqual(["processing"]);
  });
});

describe("placing an order", () => {
  it("fails closed without IndexedDB: nothing is sent", async () => {
    // @ts-expect-error: a browser without IndexedDB
    delete globalThis.indexedDB;
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ORDERED }));
    const api = gateway({});
    expect(await tab(api.fetch).flow.place(ORDERED, contact)).toEqual({ kind: "blocked", blockedBy: null, reason: "storage" });
    expect(api.calls).toEqual([]);
  });

  it("202 → polls with GET, never a second POST", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ORDERED }));
    const api = gateway({
      POST: (call) => json(202, result(call.key, "processing")),
      GET: (call) => json(200, result(refOf(call), "confirmed")),
    });
    const { flow, attempts, cart } = tab(api.fetch);
    const placed = await flow.place(ORDERED, contact);
    expect(placed).toMatchObject({ kind: "definite", result: { outcome: "processing" } });
    const record = attempts.unconfirmed()[0];
    if (!record) throw new Error("processing attempt not kept");
    expect(record.status).toBe("processing");
    expect(await flow.follow(record).done).toBe("definite");
    expect(api.count("POST")).toBe(1);
    expect(cart.getLines()).toEqual([]);
  });
});

describe("R4: a confirmed order is re-read by reference, read-only", () => {
  it("shows the confirmed result again without resolving or touching the cart", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: [{ product_id: "sku-005", quantity: 1 }] }));
    const api = gateway({ GET: () => json(200, result("ATT-1A2B-0007", "confirmed")) });
    const { flow, cart } = tab(api.fetch);
    expect(await flow.restore("ATT-1A2B-0007")).toBe(true);
    expect(flow.getSession()).toMatchObject({ phase: "done", attemptRef: "ATT-1A2B-0007", classification: { result: { order_number: "OBS-1042-7F3A" } } });
    expect(api.calls).toEqual([{ method: "GET", path: "/api/attempts/ATT-1A2B-0007", query: { customer_id: CUSTOMER }, body: "", key: "" }]);
    expect(cart.getLines()).toEqual([{ product_id: "sku-005", quantity: 1 }]);
    expect(await held()).toEqual([]);
  });

  it.each([
    ["not found", () => json(404, envelope("attempt_not_found"))],
    ["not confirmed", () => json(200, result("ATT-1A2B-0007", "payment_failed"))],
    ["a network failure", NETWORK],
    ["another reference", () => json(200, result("ATT-1A2B-0008", "confirmed"))],
  ])("anything but a matching confirmed order (%s) restores nothing and never resolves", async (_name, answer) => {
    const api = gateway({ GET: answer });
    const { flow } = tab(api.fetch);
    expect(await flow.restore("ATT-1A2B-0007")).toBe(false);
    expect(flow.getSession()).toEqual({ phase: "idle" });
    expect(api.count("RESOLVE")).toBe(0);
  });

  it("refuses a malformed reference without asking", async () => {
    const api = gateway({});
    expect(await tab(api.fetch).flow.restore("ATT-1A2B-0007&customer_id=x")).toBe(false);
    expect(api.calls).toEqual([]);
  });
});

describe("crash safety of the cart effect (D4)", () => {
  it("a confirmation journalled before a crash, whose cart write was lost, is applied once on the next start", async () => {
    const record = await unknownAttempt();
    // The gate step committed, then the page died before the cart was written.
    await createAttemptStore().confirm(record.attempt_ref, "2026-10-27T10:04:00.000Z");
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual(ORDERED);
    const next = tab(gateway({}).fetch);
    await next.flow.replayEffects();
    expect(next.cart.getLines()).toEqual([]);
    next.cart.add("sku-001");
    await tab(gateway({}).fetch).flow.replayEffects();
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual([{ product_id: "sku-001", quantity: 1 }]);
  });

  it("an effect replayed out of order is never subtracted twice, even after many later orders", async () => {
    // E1 is journalled but its tab died before the cart; E2 (another tab) is confirmed and applied.
    const e1 = await unknownAttempt();
    await createAttemptStore().confirm(e1.attempt_ref, "2026-10-27T10:01:00.000Z");
    const e2 = await beginAttempt(ORDERED, { customerId: CUSTOMER });
    const ref2 = e2.ok ? e2.attemptRef : "";
    const other = tab(gateway({ GET: () => json(200, result(ref2, "confirmed")) }).fetch);
    await other.flow.check({ attempt_ref: ref2, customer_id: CUSTOMER, lines: ORDERED, created_at: "", status: "unknown" });
    // A later start replays E1 (applied after E2).
    await tab(gateway({}).fetch).flow.replayEffects();
    // 19 more confirmed orders, each applied at once.
    for (let i = 0; i < 19; i++) {
      const next = await beginAttempt([{ product_id: "sku-005", quantity: 1 }], { customerId: CUSTOMER });
      const ref = next.ok ? next.attemptRef : "";
      await tab(gateway({ GET: () => json(200, result(ref, "confirmed")) }).fetch).flow.check({
        attempt_ref: ref,
        customer_id: CUSTOMER,
        lines: [{ product_id: "sku-005", quantity: 1 }],
        created_at: "",
        status: "unknown",
      });
    }
    // The cart now holds units the customer added after all of that; a new start must keep them.
    localStorage.setItem(
      CART_STORAGE_KEY,
      JSON.stringify({ ...JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "{}"), lines: ORDERED }),
    );
    await tab(gateway({}).fetch).flow.replayEffects();
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual(ORDERED);
  });

  it("a journalled effect older than a day is not replayed", async () => {
    const record = await unknownAttempt();
    await createAttemptStore().confirm(record.attempt_ref, "2026-10-25T10:00:00.000Z");
    const next = tab(gateway({}).fetch);
    await next.flow.replayEffects();
    expect(next.cart.getLines()).toEqual(ORDERED);
  });

  it("the journal holds lines and references only", async () => {
    const record = await unknownAttempt();
    await tab(gateway({ GET: () => json(200, result(record.attempt_ref, "confirmed")) }).fetch).flow.check(record);
    const db = await openGate();
    expect(await gateEffects(db as IDBDatabase)).toEqual([{ attempt_ref: record.attempt_ref, lines: ORDERED, confirmed_at: "2026-10-27T10:05:00.000Z" }]);
  });
});

describe("privacy", () => {
  it("after placing, an unknown answer and recovery, no contact detail is stored anywhere", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ORDERED }));
    const api = gateway({ POST: NETWORK, GET: () => json(404, envelope("attempt_not_found")), RESOLVE: (call) => json(409, envelope("attempt_abandoned", result(refOf(call), "abandoned"))) });
    const { flow, attempts } = tab(api.fetch);
    await flow.place(ORDERED, contact);
    const record = attempts.unconfirmed()[0];
    if (!record) throw new Error("unknown attempt not kept");
    await flow.check(record);
    const db = (await openGate()) as IDBDatabase;
    const everything = JSON.stringify({
      local: { ...localStorage },
      session: { ...sessionStorage },
      attempts: await gateList(db),
      effects: await gateEffects(db),
      url: location.href,
      recovery: api.calls.filter((c) => c.method === "GET" || c.path.endsWith("/resolve")),
    });
    for (const secret of [contact.name, contact.email, "Demo", "example.com"]) expect(everything).not.toContain(secret);
    expect(localStorage.getItem(ATTEMPTS_KEY)).toBe(JSON.stringify({ version: 1, attempts: [] }));
  });
});

describe("reconciling with My Orders (P4-F)", () => {
  it("a quiet check (from My Orders) settles the attempt but leaves the checkout session alone", async () => {
    const record = await unknownAttempt();
    const { flow } = tab(gateway({ GET: () => json(200, result(record.attempt_ref, "payment_failed")) }).fetch);
    expect(await flow.check(record, { quiet: true })).toMatchObject({ kind: "definite" });
    expect(flow.getSession()).toEqual({ phase: "idle" });
    expect(await held()).toEqual([]);
  });

  it("a POST that times out never downgrades an attempt another tab already knows is processing", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ORDERED }));
    let fail: (error: Error) => void = () => undefined;
    const api = gateway({ POST: () => new Promise<Response>((_, reject) => (fail = reject)) });
    const a = tab(api.fetch);
    const placing = a.flow.place(ORDERED, contact);
    await vi.waitFor(() => expect(api.count("POST")).toBe(1));
    const record = (await held())[0] as unknown as AttemptRecord;
    // Tab B reads the list: the server already has it, processing.
    expect(await tab(gateway({}).fetch).flow.reconcile(record, result(record.attempt_ref, "processing"), CUSTOMER)).toBe(true);
    fail(new TypeError("Failed to fetch"));
    expect(await placing).toMatchObject({ kind: "unknown" });
    expect((await held()).map((a) => a.status)).toEqual(["processing"]);
  });

  it("a held unknown attempt the server lists as confirmed is settled and its cart effect applied once, sending nothing", async () => {
    const record = await unknownAttempt();
    const api = gateway({});
    const { flow, cart } = tab(api.fetch);
    cart.add("sku-003"); // added while the outcome was unknown: stays
    expect(await flow.reconcile(record, result(record.attempt_ref, "confirmed"), CUSTOMER)).toBe(true);
    expect(await held()).toEqual([]);
    expect(cart.getLines()).toEqual([{ product_id: "sku-003", quantity: 1 }]);
    // Again, and then a Check status answer for the same order: nothing more comes out.
    cart.add("sku-001");
    expect(await flow.reconcile(record, result(record.attempt_ref, "confirmed"), CUSTOMER)).toBe(false);
    await tab(gateway({ GET: () => json(200, result(record.attempt_ref, "confirmed")) }).fetch).flow.check(record);
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual([
      { product_id: "sku-003", quantity: 1 },
      { product_id: "sku-001", quantity: 1 },
    ]);
    expect(api.calls).toEqual([]);
  });

  it("two tabs reconciling and checking the same confirmed order at once take the units out once", async () => {
    const record = await unknownAttempt();
    const a = tab(gateway({}).fetch);
    const b = tab(gateway({ GET: () => json(200, result(record.attempt_ref, "confirmed")) }).fetch);
    a.cart.add("sku-001");
    await Promise.all([a.flow.reconcile(record, result(record.attempt_ref, "confirmed"), CUSTOMER), b.flow.check(record)]);
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "").lines).toEqual([{ product_id: "sku-001", quantity: 1 }]);
  });

  it("a declined result settles the attempt and keeps the cart; processing only marks it", async () => {
    const record = await unknownAttempt();
    const { flow, cart } = tab(gateway({}).fetch);
    expect(await flow.reconcile(record, result(record.attempt_ref, "processing"), CUSTOMER)).toBe(true);
    expect((await held()).map((a) => a.status)).toEqual(["processing"]);
    expect(await flow.reconcile(record, result(record.attempt_ref, "payment_failed", { error_code: "payment_declined" }), CUSTOMER)).toBe(true);
    expect(await held()).toEqual([]);
    expect(cart.getLines()).toEqual(ORDERED);
  });

  it.each([
    ["listed for another customer ID", (ref: string) => result(ref, "confirmed"), "web-zzzzzzzzzzzzzzzzzzzzzz"],
    ["for another reference", () => result("ATT-FFFF-0001", "confirmed"), CUSTOMER],
    ["malformed (confirmed without an order number)", (ref: string) => result(ref, "confirmed", { order_number: null }), CUSTOMER],
    ["not a result at all", () => ({ error_code: "x" }), CUSTOMER],
  ])("a list entry %s changes nothing", async (_name, entry, listedFor) => {
    const record = await unknownAttempt();
    const { flow, cart } = tab(gateway({}).fetch);
    expect(await flow.reconcile(record, entry(record.attempt_ref), listedFor)).toBe(false);
    expect((await held()).map((a) => a.status)).toEqual(["unknown"]);
    expect(cart.getLines()).toEqual(ORDERED);
  });

  it("a confirmed order this browser no longer holds takes nothing from the cart", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ORDERED }));
    const { flow, cart } = tab(gateway({}).fetch);
    const gone: AttemptRecord = { attempt_ref: "ATT-1A2B-0099", customer_id: CUSTOMER, lines: ORDERED, created_at: "", status: "unknown" };
    expect(await flow.reconcile(gone, result("ATT-1A2B-0099", "confirmed"), CUSTOMER)).toBe(false);
    expect(cart.getLines()).toEqual(ORDERED);
  });

  it("a very old unknown attempt confirmed today is journalled today: a crash replay still applies it, once (U8)", async () => {
    const record = await unknownAttempt();
    const old = { ...record, created_at: "2025-01-01T00:00:00.000Z" };
    // The gate step commits; the cart write is lost (crash) — simulated by confirming directly.
    await createAttemptStore().confirm(old.attempt_ref, "2026-10-27T10:05:00.000Z");
    const next = tab(gateway({}).fetch);
    await next.flow.replayEffects();
    await next.flow.replayEffects();
    expect(next.cart.getLines()).toEqual([]);
  });
});
