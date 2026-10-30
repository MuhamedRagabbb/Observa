// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/checkout"}
// Placing an order: the attempt is recorded before the single POST, the answer is classified
// by outcome.ts, the cart is cleared only on a confirmed order, and contact details are sent
// once and never stored (U1, U6, U7).
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createCartStore, CART_STORAGE_KEY } from "../src/store/cart/cart-store.ts";
import { ATTEMPTS_KEY, beginAttempt, createAttemptStore } from "../src/store/checkout/attempts.ts";
import { createCheckoutFlow } from "../src/store/checkout/flow.ts";
import { gateList, openGate, resetGate } from "../src/store/checkout/gate.ts";
import type { CheckoutResult } from "../src/store/checkout/outcome.ts";

const contact = { name: "Demo Person", email: "demo.person@example.com" };
const LINES = [
  { product_id: "sku-001", quantity: 1 },
  { product_id: "sku-002", quantity: 2 },
];

type Seen = { request: Request; body: Record<string, unknown>; storedAttempts: string | null };

function result(attempt_ref: string, outcome: CheckoutResult["outcome"]): CheckoutResult {
  return {
    attempt_ref,
    order_id: outcome === "confirmed" ? "order-0f8fad5b-d9cb-469f-a165-70867728950e" : null,
    order_number: outcome === "confirmed" ? "OBS-1042-7F3A" : null,
    state: "x",
    outcome,
    retryable: outcome !== "confirmed",
    items: [],
    total_cents: 4800,
    error_code: null,
    unavailable_items: null,
    created_at: null,
  };
}

/** A fetch that records what it saw (including storage at send time) and answers via `reply`. */
function server(reply: (ref: string) => Response | Promise<Response>) {
  const seen: Seen[] = [];
  const fetch = vi.fn(async (request: Request) => {
    const body = (await request.clone().json()) as Record<string, unknown>;
    seen.push({ request, body, storedAttempts: localStorage.getItem(ATTEMPTS_KEY) });
    return reply(request.headers.get("Idempotency-Key") ?? "");
  });
  return { fetch, seen };
}

function setup(reply: (ref: string) => Response | Promise<Response>) {
  localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: LINES }));
  const cart = createCartStore();
  const attempts = createAttemptStore();
  const { fetch, seen } = server(reply);
  const flow = createCheckoutFlow({ cart, attempts, fetch });
  return { cart, attempts, flow, seen, fetch };
}

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  resetGate();
  localStorage.clear();
});
afterEach(() => {
  vi.useRealTimers();
  resetGate();
});

describe("placing an order", () => {
  it("records the attempt before sending exactly one POST with its reference as the Idempotency-Key", async () => {
    const { flow, seen } = setup((ref) => Response.json(result(ref, "confirmed"), { status: 201 }));
    await flow.place(LINES, contact);
    expect(seen).toHaveLength(1);
    const [{ request, body, storedAttempts }] = seen as [Seen];
    const ref = request.headers.get("Idempotency-Key") ?? "";
    expect(ref).toMatch(/^ATT-[0-9A-F]{4}-[0-9]{4}$/);
    expect(new URL(request.url).pathname).toBe("/api/checkout");
    expect(JSON.parse(storedAttempts ?? "").attempts[0]).toMatchObject({ attempt_ref: ref, lines: LINES, status: "submitting" });
    expect(body).toEqual({ customer_id: expect.stringMatching(/^web-[0-9a-z]{22}$/), items: LINES, contact });
  });

  it("confirmed: settles the attempt and clears the ordered lines from the cart", async () => {
    const { flow, cart, attempts } = setup((ref) => Response.json(result(ref, "confirmed"), { status: 201 }));
    const outcome = await flow.place(LINES, contact);
    expect(outcome).toMatchObject({ kind: "definite", result: { outcome: "confirmed", order_number: "OBS-1042-7F3A" } });
    expect(attempts.unconfirmed()).toEqual([]);
    expect(cart.getLines()).toEqual([]);
  });

  it("declined: settles the attempt and keeps the cart", async () => {
    const { flow, cart, attempts } = setup((ref) =>
      Response.json({ error_code: "payment_declined", message: "x", details: result(ref, "payment_failed") }, { status: 402 }),
    );
    expect(await flow.place(LINES, contact)).toMatchObject({ kind: "definite", result: { outcome: "payment_failed" } });
    expect(attempts.unconfirmed()).toEqual([]);
    expect(cart.getLines()).toEqual(LINES);
  });

  it("processing (202): keeps the attempt as processing and keeps the cart", async () => {
    const { flow, cart, attempts } = setup((ref) => Response.json(result(ref, "processing"), { status: 202 }));
    await flow.place(LINES, contact);
    expect(attempts.unconfirmed().map((a) => a.status)).toEqual(["processing"]);
    expect(cart.getLines()).toEqual(LINES);
  });

  it.each([
    ["a network error", () => Promise.reject(new TypeError("Failed to fetch"))],
    ["a 502", () => Response.json({ error_code: "dependency_unavailable", message: "x" }, { status: 502 })],
    ["409 idempotency_conflict without a result", () => Response.json({ error_code: "idempotency_conflict", message: "x" }, { status: 409 })],
    ["a non-JSON body", () => new Response("<html>proxy</html>", { status: 201 })],
  ])("unknown after %s: keeps the attempt as unknown, keeps the cart, never retries", async (_label, reply) => {
    const { flow, cart, attempts, fetch } = setup(reply as () => Response);
    expect(await flow.place(LINES, contact)).toMatchObject({ kind: "unknown" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(attempts.unconfirmed().map((a) => a.status)).toEqual(["unknown"]);
    expect(cart.getLines()).toEqual(LINES);
  });

  it("gives up waiting after 30 seconds and treats the attempt as unknown", async () => {
    // Only the timers the flow uses: IndexedDB keeps running on real ones.
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const { flow, attempts, fetch } = setup(() => new Promise<Response>(() => undefined));
    const placing = flow.place(LINES, contact);
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    await vi.advanceTimersByTimeAsync(30_000);
    expect(await placing).toMatchObject({ kind: "unknown", reason: "network" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(attempts.unconfirmed().map((a) => a.status)).toEqual(["unknown"]);
  });

  it("a second press while the first is in flight sends nothing (double-click = one POST)", async () => {
    let answer: (response: Response) => void = () => undefined;
    const { flow, fetch } = setup(() => new Promise<Response>((resolve) => (answer = resolve)));
    const first = flow.place(LINES, contact);
    expect(await flow.place(LINES, contact)).toEqual({ kind: "busy" });
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const ref = JSON.parse(localStorage.getItem(ATTEMPTS_KEY) ?? "").attempts[0].attempt_ref;
    answer(Response.json(result(ref, "confirmed"), { status: 201 }));
    await first;
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("sends nothing while another attempt is unconfirmed (another tab, or before a reload)", async () => {
    const { flow, fetch, attempts } = setup((ref) => Response.json(result(ref, "confirmed"), { status: 201 }));
    const other = await beginAttempt(LINES, { customerId: "web-0123456789abcdefghijkl" });
    await attempts.sync();
    expect(await flow.place(LINES, contact)).toEqual({ kind: "blocked", blockedBy: other.ok ? other.attemptRef : "missing" });
    expect(fetch).not.toHaveBeenCalled();
  });

  it("a new attempt after a definite failure gets a new reference", async () => {
    const { flow, seen } = setup((ref) =>
      Response.json({ error_code: "payment_declined", message: "x", details: result(ref, "payment_failed") }, { status: 402 }),
    );
    await flow.place(LINES, contact);
    await flow.place(LINES, contact);
    const refs = seen.map((s) => s.request.headers.get("Idempotency-Key"));
    expect(refs).toHaveLength(2);
    expect(refs[0]).not.toBe(refs[1]);
  });

  it("a blocked localStorage mirror does not matter: the gate holds the attempt, which is sent once", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: LINES }));
    const { fetch } = server(() => Promise.reject(new TypeError("Failed to fetch")));
    const failing = {
      getItem: (key: string) => localStorage.getItem(key),
      setItem: (key: string, value: string) => {
        if (key === ATTEMPTS_KEY) throw new DOMException("full", "QuotaExceededError");
        localStorage.setItem(key, value);
      },
    };
    const attempts = createAttemptStore(failing);
    const flow = createCheckoutFlow({ cart: createCartStore(), attempts, fetch, storage: failing });
    expect(await flow.place(LINES, contact)).toMatchObject({ kind: "unknown" });
    expect(fetch).toHaveBeenCalledTimes(1);
    const db = await openGate();
    expect((await gateList(db as IDBDatabase))?.map((a) => a.status)).toEqual(["unknown"]);
    expect(attempts.unconfirmed().map((a) => a.status)).toEqual(["unknown"]);
  });

  it("returns to the form, sending nothing, when IndexedDB refuses to open (fail closed)", async () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: LINES }));
    const { fetch } = server((ref) => Response.json(result(ref, "confirmed"), { status: 201 }));
    const real = globalThis.indexedDB;
    globalThis.indexedDB = Object.assign(Object.create(real) as IDBFactory, {
      open: () => {
        throw new DOMException("denied", "SecurityError");
      },
    });
    const flow = createCheckoutFlow({ cart: createCartStore(), attempts: createAttemptStore(), fetch });
    expect(await flow.place(LINES, contact)).toEqual({ kind: "blocked", blockedBy: null, reason: "storage" });
    expect(fetch).not.toHaveBeenCalled();
    expect(flow.getSession()).toEqual({ phase: "idle" });
  });

  it("confirmed: removes only the ordered units, keeping units added while it was placed", async () => {
    let answer: (response: Response) => void = () => undefined;
    const { flow, cart } = setup(() => new Promise<Response>((resolve) => (answer = resolve)));
    const placing = flow.place(LINES, contact);
    await vi.waitFor(() => expect(localStorage.getItem(ATTEMPTS_KEY)).toContain("ATT-"));
    cart.add("sku-002"); // a third mug, added from the home page during the request
    cart.add("sku-005");
    const ref = JSON.parse(localStorage.getItem(ATTEMPTS_KEY) ?? "").attempts[0].attempt_ref;
    answer(Response.json(result(ref, "confirmed"), { status: 201 }));
    await placing;
    expect(cart.getLines()).toEqual([
      { product_id: "sku-002", quantity: 1 },
      { product_id: "sku-005", quantity: 1 },
    ]);
  });

  it("never writes contact details to storage", async () => {
    const { flow } = setup(() => Promise.reject(new TypeError("Failed to fetch")));
    await flow.place(LINES, contact);
    const everything = Object.keys(localStorage)
      .map((key) => `${key}=${localStorage.getItem(key)}`)
      .join("\n");
    expect(everything).not.toContain(contact.name);
    expect(everything).not.toContain(contact.email);
    expect(everything).not.toContain("demo.person");
  });
});
