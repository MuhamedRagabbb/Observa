// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/orders"}
// The note a browser keeps for an attempt that Check status found was never placed
// (src/store/checkout/closed.ts). The server does not list such attempts, so this is the only
// thing that lets My Orders say "Not placed" after the row's unconfirmed record is gone.
// It is display only: it never decides whether anything may be sent, and holds no contact data.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CLOSED_KEY, CLOSED_MAX, CLOSED_TTL_MS, createClosedStore } from "../src/store/checkout/closed.ts";

const CUSTOMER = "web-0123456789abcdefghijkl";
const LINES = [
  { product_id: "sku-001", quantity: 1 },
  { product_id: "sku-002", quantity: 2 },
];
const NOW = Date.parse("2026-10-28T12:00:00.000Z");
const attempt = (n: number) => ({
  attempt_ref: `ATT-1A2B-${String(n).padStart(4, "0")}`,
  customer_id: CUSTOMER,
  lines: LINES,
  created_at: "2026-10-28T11:55:00.000Z",
});

let clock = NOW;
const make = (storage: Pick<Storage, "getItem" | "setItem"> = localStorage) => createClosedStore(storage, () => clock);

beforeEach(() => {
  clock = NOW;
  localStorage.clear();
});
afterEach(() => vi.restoreAllMocks());

describe("closed attempts", () => {
  it("keeps a note per reference, newest first, and a reload (a new store) reads it back", () => {
    const store = make();
    store.add(attempt(1));
    clock += 1000;
    store.add(attempt(2));
    expect(store.list().map((n) => n.attempt_ref)).toEqual(["ATT-1A2B-0002", "ATT-1A2B-0001"]);
    expect(store.list()[0]).toEqual({ ...attempt(2), closed_at: "2026-10-28T12:00:01.000Z" });
    expect(make().list()).toEqual(store.list());
  });

  it("adding a reference again keeps one note, not two", () => {
    const store = make();
    store.add(attempt(1));
    store.add(attempt(1));
    expect(store.list()).toHaveLength(1);
  });

  it("stores only the reference, customer ID, lines and times: nothing else rides along (no contact data)", () => {
    const store = make();
    store.add({ ...attempt(1), contact: { name: "Demo Person", email: "demo@example.com" }, extra: 1 } as never);
    const raw = localStorage.getItem(CLOSED_KEY) ?? "";
    expect(raw).not.toMatch(/Demo Person|demo@example\.com|extra|contact/);
    expect(Object.keys(JSON.parse(raw).notes[0]).sort()).toEqual(["attempt_ref", "closed_at", "created_at", "customer_id", "lines"]);
  });

  it(`keeps the ${CLOSED_MAX} newest`, () => {
    const store = make();
    for (let n = 1; n <= CLOSED_MAX + 3; n++) {
      clock += 1000;
      store.add(attempt(n));
    }
    expect(store.list()).toHaveLength(CLOSED_MAX);
    expect(store.list()[0]?.attempt_ref).toBe(`ATT-1A2B-${String(CLOSED_MAX + 3).padStart(4, "0")}`);
    expect(store.list().some((n) => n.attempt_ref === "ATT-1A2B-0001")).toBe(false);
  });

  it("forgets a note after 24 hours (it is only a reminder, not a record)", () => {
    make().add(attempt(1));
    clock += CLOSED_TTL_MS - 1;
    expect(make().list()).toHaveLength(1);
    clock += 1;
    expect(make().list()).toEqual([]);
  });

  it("dismiss removes the note, also for the next page start", () => {
    const store = make();
    store.add(attempt(1));
    store.add(attempt(2));
    store.dismiss("ATT-1A2B-0001");
    expect(store.list().map((n) => n.attempt_ref)).toEqual(["ATT-1A2B-0002"]);
    expect(make().list().map((n) => n.attempt_ref)).toEqual(["ATT-1A2B-0002"]);
  });

  it.each([
    ["not JSON", "{nope"],
    ["another version", JSON.stringify({ version: 2, notes: [{ ...attempt(1), closed_at: "2026-10-28T12:00:00.000Z" }] })],
    ["a bad reference", JSON.stringify({ version: 1, notes: [{ ...attempt(1), attempt_ref: "ATT-1", closed_at: "2026-10-28T12:00:00.000Z" }] })],
    ["a bad product", JSON.stringify({ version: 1, notes: [{ ...attempt(1), lines: [{ product_id: "x", quantity: 1 }], closed_at: "2026-10-28T12:00:00.000Z" }] })],
    ["a bad customer", JSON.stringify({ version: 1, notes: [{ ...attempt(1), customer_id: "someone", closed_at: "2026-10-28T12:00:00.000Z" }] })],
    ["a bad time", JSON.stringify({ version: 1, notes: [{ ...attempt(1), closed_at: "yesterday" }] })],
  ])("ignores stored data that is %s", (_name, raw) => {
    localStorage.setItem(CLOSED_KEY, raw);
    expect(make().list()).toEqual([]);
  });

  it("another tab's change arrives through the storage event, and subscribers hear it", () => {
    const store = make();
    const heard = vi.fn();
    const unsubscribe = store.subscribe(heard);
    const other = make();
    other.add(attempt(1));
    window.dispatchEvent(new StorageEvent("storage", { key: CLOSED_KEY }));
    expect(store.list().map((n) => n.attempt_ref)).toEqual(["ATT-1A2B-0001"]);
    expect(heard).toHaveBeenCalledTimes(1);
    unsubscribe();
    other.add(attempt(2));
    window.dispatchEvent(new StorageEvent("storage", { key: CLOSED_KEY }));
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("with storage blocked the note still shows for this page", () => {
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    const store = make(blocked);
    store.add(attempt(1));
    expect(store.list().map((n) => n.attempt_ref)).toEqual(["ATT-1A2B-0001"]);
  });

  it("the list is the same array until something changes (it feeds useSyncExternalStore)", () => {
    const store = make();
    store.add(attempt(1));
    const first = store.list();
    expect(store.list()).toBe(first);
    store.add(attempt(1));
    expect(store.list()).toBe(first);
  });
});

describe("closed attempts across tabs and blocked storage (review findings)", () => {
  it("a tab with an old copy never brings back a note another tab dismissed", () => {
    const a = make();
    const b = make();
    b.subscribe(() => undefined);
    a.add(attempt(1));
    window.dispatchEvent(new StorageEvent("storage", { key: CLOSED_KEY }));
    expect(b.list().map((n) => n.attempt_ref)).toEqual(["ATT-1A2B-0001"]);
    // B's copy goes stale: A dismisses, B has not heard yet (no storage event delivered to it).
    localStorage.setItem(CLOSED_KEY, JSON.stringify({ version: 1, notes: [] }));
    clock += 1000;
    b.add(attempt(2));
    expect(JSON.parse(localStorage.getItem(CLOSED_KEY) ?? "").notes.map((n: { attempt_ref: string }) => n.attempt_ref)).toEqual(["ATT-1A2B-0002"]);
    expect(b.list().map((n) => n.attempt_ref)).toEqual(["ATT-1A2B-0002"]);
  });

  it("with storage blocked, dismissing one note keeps the others for this page", () => {
    const blocked = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    const store = make(blocked);
    store.add(attempt(1));
    clock += 1000;
    store.add(attempt(2));
    store.dismiss("ATT-1A2B-0001");
    expect(store.list().map((n) => n.attempt_ref)).toEqual(["ATT-1A2B-0002"]);
  });
});
