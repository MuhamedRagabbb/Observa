// @vitest-environment jsdom
// The checkout gate (src/store/checkout/gate.ts) through beginAttempt and the attempt store,
// against a real IndexedDB implementation (fake-indexeddb). Each test gets a fresh database.
// Two "tabs" are two connections to the same database, which is what two tabs of one origin are.
import "fake-indexeddb/auto";
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ATTEMPTS_KEY, beginAttempt, createAttemptStore } from "../src/store/checkout/attempts.ts";
import { EFFECTS_KEPT, gateBegin, gateConfirm, gateEffects, gateList, openGate, resetGate } from "../src/store/checkout/gate.ts";

const LINES = [{ product_id: "sku-001", quantity: 1 }];
const CUSTOMER = "web-0123456789abcdefghijkl";
const ATT = /^ATT-[0-9A-F]{4}-[0-9]{4}$/;
const AT = "2026-10-27T10:00:00.000Z";

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  resetGate();
  localStorage.clear();
});
afterEach(() => resetGate());

async function gate(): Promise<IDBDatabase> {
  const db = await openGate();
  if (!db) throw new Error("gate did not open");
  return db;
}

/** A second tab: another connection to the same database (the schema exists once openGate ran). */
function secondTab(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("observa-checkout", 2);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

/** Forwards every property to the real object, except the ones `override` replaces. */
function forward<T extends object>(target: T, override: (key: PropertyKey) => unknown): T {
  return new Proxy(target, {
    get(t, key) {
      const replaced = override(key);
      if (replaced !== undefined) return replaced;
      const value = Reflect.get(t, key, t);
      return typeof value === "function" ? value.bind(t) : value;
    },
    set: (t, key, value) => Reflect.set(t, key, value, t),
  });
}

/**
 * The gate with its check removed (test-only mutant): inside a read-write transaction the
 * attempts store always reads as empty. Used to show the race check can fail.
 */
function withoutCheck(db: IDBDatabase): IDBDatabase {
  return forward(db, (key) =>
    key !== "transaction"
      ? undefined
      : (stores: string | string[], mode?: IDBTransactionMode) => {
          const tx = db.transaction(stores, mode);
          return forward(tx, (k) =>
            k !== "objectStore"
              ? undefined
              : (name: string) => {
                  const store = tx.objectStore(name);
                  return name !== "attempts" || mode !== "readwrite"
                    ? store
                    : forward(store, (s) => (s === "getAll" ? () => store.getAll(IDBKeyRange.only("~none~")) : undefined));
                },
          );
        },
  );
}

/** Two tabs press Place Order at the same moment: how many may send? */
async function race(tabA: IDBDatabase, tabB: IDBDatabase) {
  const results = await Promise.all([
    gateBegin(tabA, { customer_id: CUSTOMER, lines: LINES, created_at: AT }),
    gateBegin(tabB, { customer_id: CUSTOMER, lines: LINES, created_at: AT }),
  ]);
  return results.filter((r) => r.ok).length;
}

describe("fail closed", () => {
  it("refuses to start an attempt without IndexedDB, and records nothing", async () => {
    // @ts-expect-error: a browser without IndexedDB
    delete globalThis.indexedDB;
    const started = await beginAttempt(LINES, { customerId: CUSTOMER });
    expect(started).toEqual({ ok: false, blockedBy: null, reason: "storage" });
    expect(localStorage.getItem(ATTEMPTS_KEY)).toBeNull();
  });

  it("refuses when IndexedDB will not open, then tries again next time", async () => {
    const real = globalThis.indexedDB;
    globalThis.indexedDB = forward(real, (key) =>
      key === "open"
        ? () => {
            throw new DOMException("denied", "SecurityError");
          }
        : undefined,
    );
    expect(await beginAttempt(LINES, { customerId: CUSTOMER })).toEqual({ ok: false, blockedBy: null, reason: "storage" });
    globalThis.indexedDB = real;
    expect(await beginAttempt(LINES, { customerId: CUSTOMER })).toEqual({ ok: true, attemptRef: expect.stringMatching(ATT) });
  });
});

describe("two tabs at the same moment", () => {
  it("only one may send", async () => {
    const tabA = await gate();
    expect(await race(tabA, await secondTab())).toBe(1);
  });

  it("the same check lets both send when the gate's check is removed (so it can detect a broken gate)", async () => {
    const tabA = await gate();
    expect(await race(withoutCheck(tabA), withoutCheck(await secondTab()))).toBe(2);
  });

  it("the same check catches a gate that reads and writes in separate transactions (a race-only bug)", async () => {
    const nonAtomic = async (db: IDBDatabase) => {
      const held = await gateList(db);
      if (held && held.length > 0) return { ok: false };
      return gateBegin(db, { customer_id: CUSTOMER, lines: LINES, created_at: AT });
    };
    const tabA = await gate();
    const results = await Promise.all([nonAtomic(tabA), nonAtomic(await secondTab())]);
    expect(results.filter((r) => r.ok)).toHaveLength(1);
  });

  it("the refused tab is told which attempt blocks it", async () => {
    const tabA = await gate();
    const [first, second] = await Promise.all([
      gateBegin(tabA, { customer_id: CUSTOMER, lines: LINES, created_at: AT }),
      gateBegin(await secondTab(), { customer_id: CUSTOMER, lines: LINES, created_at: AT }),
    ]);
    expect(first.ok && second.ok).toBe(false);
    const blocked = first.ok ? second : first;
    const sent = first.ok ? first : second;
    expect(blocked).toEqual({ ok: false, blockedBy: sent.ok ? sent.record.attempt_ref : "missing" });
  });
});

describe("starting an attempt", () => {
  it("stores the reference, the customer ID it is sent with, lines and time: nothing else", async () => {
    const started = await beginAttempt(LINES, { customerId: CUSTOMER, now: () => AT });
    expect(started).toEqual({ ok: true, attemptRef: expect.stringMatching(ATT) });
    expect(await gateList(await gate())).toEqual([
      { attempt_ref: started.ok ? started.attemptRef : "", customer_id: CUSTOMER, lines: LINES, created_at: AT, status: "submitting" },
    ]);
  });

  it("refuses an empty cart", async () => {
    expect(await beginAttempt([], { customerId: CUSTOMER })).toEqual({ ok: false, blockedBy: null });
  });

  it("after 9999 attempts moves to a new prefix instead of wrapping", async () => {
    const db = await gate();
    await new Promise<void>((resolve) => {
      const tx = db.transaction("meta", "readwrite");
      tx.objectStore("meta").put({ prefix: "ABCD", next: 9999 }, "attempt-ref");
      tx.oncomplete = () => resolve();
    });
    const store = createAttemptStore();
    const first = await beginAttempt(LINES, { customerId: CUSTOMER });
    expect(first).toEqual({ ok: true, attemptRef: "ATT-ABCD-9999" });
    await store.settle("ATT-ABCD-9999");
    const next = await beginAttempt(LINES, { customerId: CUSTOMER });
    expect(next.ok && next.attemptRef).toMatch(/^ATT-(?!ABCD)[0-9A-F]{4}-0001$/);
  });

  it("a stored record this version cannot read still blocks", async () => {
    const db = await gate();
    await new Promise<void>((resolve) => {
      const tx = db.transaction("attempts", "readwrite");
      tx.objectStore("attempts").put({ attempt_ref: "ATT-1A2B-0001", future_field: true });
      tx.oncomplete = () => resolve();
    });
    expect(await beginAttempt(LINES, { customerId: CUSTOMER })).toEqual({ ok: false, blockedBy: "ATT-1A2B-0001" });
  });
});

describe("the attempt list and its mirror", () => {
  it("reads the gate, so a cleared mirror loses nothing", async () => {
    const started = await beginAttempt(LINES, { customerId: CUSTOMER, now: () => AT });
    localStorage.removeItem(ATTEMPTS_KEY);
    const store = createAttemptStore();
    expect(store.unconfirmed()).toEqual([]);
    await store.sync();
    expect(store.unconfirmed().map((a) => a.attempt_ref)).toEqual([started.ok ? started.attemptRef : "missing"]);
    expect(JSON.parse(localStorage.getItem(ATTEMPTS_KEY) ?? "").attempts).toHaveLength(1);
  });

  it("a tab with an old copy cannot bring a settled attempt back", async () => {
    const started = await beginAttempt(LINES, { customerId: CUSTOMER, now: () => AT });
    const ref = started.ok ? started.attemptRef : "";
    const stale = createAttemptStore();
    await stale.sync();
    const fresh = createAttemptStore();
    await fresh.sync();
    await fresh.settle(ref);
    // The stale tab still lists it and now writes a status change of its own.
    expect(stale.unconfirmed()).toHaveLength(1);
    await stale.mark(ref, "unknown");
    expect(stale.unconfirmed()).toEqual([]);
    expect(JSON.parse(localStorage.getItem(ATTEMPTS_KEY) ?? "").attempts).toEqual([]);
    expect(await gateList(await gate())).toEqual([]);
  });

  it("never keeps unknown fields such as contact details", async () => {
    localStorage.setItem(
      ATTEMPTS_KEY,
      JSON.stringify({
        version: 1,
        attempts: [{ attempt_ref: "ATT-1A2B-0003", customer_id: CUSTOMER, lines: LINES, created_at: AT, status: "unknown", email: "a@b.c" }],
      }),
    );
    expect(createAttemptStore().unconfirmed()).toEqual([
      { attempt_ref: "ATT-1A2B-0003", customer_id: CUSTOMER, lines: LINES, created_at: AT, status: "unknown" },
    ]);
  });
});

describe("confirming", () => {
  it("removes the attempt and journals its cart effect in one step", async () => {
    const started = await beginAttempt(LINES, { customerId: CUSTOMER, now: () => AT });
    const ref = started.ok ? started.attemptRef : "";
    const db = await gate();
    expect(await gateConfirm(db, ref, AT)).toEqual({ ok: true, effect: { attempt_ref: ref, lines: LINES, confirmed_at: AT } });
    expect(await gateList(db)).toEqual([]);
    expect(await gateEffects(db)).toEqual([{ attempt_ref: ref, lines: LINES, confirmed_at: AT }]);
  });

  it("a second confirmation (another tab, a replay) returns the same effect, never a new one", async () => {
    const started = await beginAttempt(LINES, { customerId: CUSTOMER, now: () => AT });
    const ref = started.ok ? started.attemptRef : "";
    const [a, b] = await Promise.all([gateConfirm(await gate(), ref, AT), gateConfirm(await secondTab(), ref, "2026-10-27T10:00:05.000Z")]);
    expect(a.effect).toEqual(b.effect);
    expect(await gateEffects(await gate())).toHaveLength(1);
  });

  it("an answer for an attempt this browser no longer holds takes nothing from the cart", async () => {
    expect(await gateConfirm(await gate(), "ATT-1A2B-0009", AT)).toEqual({ ok: true, effect: null });
    expect(await gateEffects(await gate())).toEqual([]);
  });

  it(`keeps the newest ${EFFECTS_KEPT} effects`, async () => {
    const store = createAttemptStore();
    for (let i = 0; i < EFFECTS_KEPT + 3; i++) {
      const started = await beginAttempt(LINES, { customerId: CUSTOMER });
      await store.confirm(started.ok ? started.attemptRef : "", `2026-10-27T10:${String(i).padStart(2, "0")}:00.000Z`);
    }
    const kept = await gateEffects(await gate());
    expect(kept).toHaveLength(EFFECTS_KEPT);
    expect(kept.map((e) => e.confirmed_at).sort()[0]).toBe("2026-10-27T10:03:00.000Z");
  });
});
