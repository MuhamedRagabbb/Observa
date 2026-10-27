// @vitest-environment jsdom
// Browser-side checkout bookkeeping: the anonymous customer ID, attempt references and the
// unconfirmed-attempt records that are written before the checkout POST.
import { afterEach, describe, expect, it } from "vitest";
import {
  ATTEMPTS_KEY,
  allocateReference,
  beginAttempt,
  createAttemptStore,
  customerId,
  REFERENCE_KEY,
  type LockManager,
} from "../src/store/checkout/attempts.ts";

afterEach(() => localStorage.clear());

const ATT = /^ATT-[0-9A-F]{4}-[0-9]{4}$/; // services/shared/checkout.py ATTEMPT_REF_PATTERN

/** A Web Locks stand-in that really serialises callbacks, like navigator.locks across tabs. */
function fakeLocks(): LockManager {
  let tail = Promise.resolve();
  return {
    request: <T>(_name: string, callback: () => Promise<T> | T) => {
      const run = tail.then(() => callback());
      tail = run.then(
        () => undefined,
        () => undefined,
      );
      return run;
    },
  };
}

describe("customer ID", () => {
  it("is one anonymous web- ID per browser, in the form the backend accepts", () => {
    const id = customerId();
    expect(id).toMatch(/^web-[0-9a-z]{22}$/);
    expect(id).toMatch(/^[a-zA-Z0-9_-]{1,64}$/);
    expect(customerId()).toBe(id);
  });

  it("replaces a stored value the backend would reject", () => {
    localStorage.setItem("observa.customer", "bad id!");
    expect(customerId()).toMatch(/^web-[0-9a-z]{22}$/);
  });
});

describe("attempt references", () => {
  it("are ATT-PPPP-NNNN with a random prefix and a counter that only moves forward", async () => {
    const locks = fakeLocks();
    const first = await allocateReference(localStorage, locks);
    const second = await allocateReference(localStorage, locks);
    expect(first).toMatch(ATT);
    expect(second).toMatch(ATT);
    expect(second.slice(0, 8)).toBe(first.slice(0, 8));
    expect(Number(second.slice(9))).toBe(Number(first.slice(9)) + 1);
  });

  it("never hands out the same reference twice, even when asked at the same moment", async () => {
    const locks = fakeLocks();
    const refs = await Promise.all(Array.from({ length: 25 }, () => allocateReference(localStorage, locks)));
    expect(new Set(refs).size).toBe(25);
  });

  it("starts a new prefix rather than wrapping the counter", async () => {
    localStorage.setItem(REFERENCE_KEY, JSON.stringify({ prefix: "ABCD", next: 9999 }));
    const locks = fakeLocks();
    expect(await allocateReference(localStorage, locks)).toBe("ATT-ABCD-9999");
    const next = await allocateReference(localStorage, locks);
    expect(next).toMatch(ATT);
    expect(next.startsWith("ATT-ABCD-")).toBe(false);
    expect(next.endsWith("-0001")).toBe(true);
  });

  it("starts afresh from a corrupt stored counter", async () => {
    localStorage.setItem(REFERENCE_KEY, "{not json");
    expect(await allocateReference(localStorage, fakeLocks())).toMatch(/^ATT-[0-9A-F]{4}-0001$/);
  });
});

describe("unconfirmed attempts", () => {
  const lines = [{ product_id: "sku-002", quantity: 2 }];

  it("records the reference, lines and time, and nothing else (never contact details)", () => {
    const store = createAttemptStore();
    store.record({ attempt_ref: "ATT-1A2B-0001", lines, created_at: "2026-10-27T10:00:00.000Z" });
    const stored = JSON.parse(localStorage.getItem(ATTEMPTS_KEY) ?? "");
    expect(stored).toEqual({
      version: 1,
      attempts: [{ attempt_ref: "ATT-1A2B-0001", lines, created_at: "2026-10-27T10:00:00.000Z", status: "submitting" }],
    });
  });

  it("keeps an attempt until a definite outcome settles it", () => {
    const store = createAttemptStore();
    store.record({ attempt_ref: "ATT-1A2B-0001", lines, created_at: "2026-10-27T10:00:00.000Z" });
    store.mark("ATT-1A2B-0001", "unknown");
    expect(store.unconfirmed().map((a) => [a.attempt_ref, a.status])).toEqual([["ATT-1A2B-0001", "unknown"]]);
    store.settle("ATT-1A2B-0001");
    expect(store.unconfirmed()).toEqual([]);
  });

  it("is shared with other tabs", () => {
    const store = createAttemptStore();
    const seen: number[] = [];
    const stop = store.subscribe(() => seen.push(store.unconfirmed().length));
    const value = JSON.stringify({
      version: 1,
      attempts: [{ attempt_ref: "ATT-1A2B-0002", lines, created_at: "2026-10-27T10:00:00.000Z", status: "submitting" }],
    });
    localStorage.setItem(ATTEMPTS_KEY, value);
    window.dispatchEvent(new StorageEvent("storage", { key: ATTEMPTS_KEY, newValue: value }));
    expect(seen).toEqual([1]);
    stop();
  });

  it("drops malformed records but keeps valid ones", () => {
    localStorage.setItem(
      ATTEMPTS_KEY,
      JSON.stringify({
        version: 1,
        attempts: [
          { attempt_ref: "nope", lines, created_at: "x", status: "submitting" },
          { attempt_ref: "ATT-1A2B-0003", lines, created_at: "2026-10-27T10:00:00.000Z", status: "processing" },
          { attempt_ref: "ATT-1A2B-0004", lines: "x", created_at: "2026-10-27T10:00:00.000Z", status: "unknown" },
        ],
      }),
    );
    expect(createAttemptStore().unconfirmed().map((a) => a.attempt_ref)).toEqual(["ATT-1A2B-0003"]);
  });
});

describe("starting an attempt", () => {
  const lines = [{ product_id: "sku-001", quantity: 1 }];

  it("allocates a reference and records the attempt before anything is sent", async () => {
    const started = await beginAttempt(lines, { storage: localStorage, locks: fakeLocks(), now: () => "2026-10-27T10:00:00.000Z" });
    expect(started).toEqual({ ok: true, attemptRef: expect.stringMatching(ATT) });
    const ref = started.ok ? started.attemptRef : "";
    expect(createAttemptStore().unconfirmed()).toEqual([
      { attempt_ref: ref, lines, created_at: "2026-10-27T10:00:00.000Z", status: "submitting" },
    ]);
  });

  it("refuses while another attempt is unconfirmed, even when two start at the same moment", async () => {
    const locks = fakeLocks();
    const results = await Promise.all([beginAttempt(lines, { storage: localStorage, locks }), beginAttempt(lines, { storage: localStorage, locks })]);
    expect(results.filter((r) => r.ok)).toHaveLength(1);
    expect(results.filter((r) => !r.ok)).toEqual([{ ok: false, blockedBy: expect.stringMatching(ATT) }]);
    expect(createAttemptStore().unconfirmed()).toHaveLength(1);
  });

  it("refuses an empty cart", async () => {
    expect(await beginAttempt([], { storage: localStorage, locks: fakeLocks() })).toEqual({ ok: false, blockedBy: null });
  });
});
