// The checkout gate: the one durable, cross-tab record of checkout attempts.
//
// IndexedDB runs read-write transactions on the same stores one at a time across every tab of
// this origin, and a later transaction always sees an earlier one's committed writes. So:
// - gateBegin is one transaction that refuses while any attempt is stored, advances the
//   reference counter and stores the new attempt;
// - gateConfirm is one transaction that removes a confirmed attempt and journals its cart
//   effect, so the effect can be replayed after a crash (see cart-store applyConfirmed).
// localStorage gives neither guarantee between tabs (Chromium updates other tabs' copies
// asynchronously), so it only mirrors this database for the screens. Without IndexedDB nothing
// may be sent at all (fail closed).
import type { CartLine } from "../cart/cart-store.ts";

export type AttemptStatus = "submitting" | "processing" | "unknown";
/** The customer ID is the one the attempt was sent with: Check status must use the same one. */
export type GateRecord = { attempt_ref: string; customer_id: string; lines: CartLine[]; created_at: string; status: AttemptStatus };
/** A confirmed order's cart effect, kept so it can be applied after a crash. */
export type Effect = { attempt_ref: string; lines: CartLine[]; confirmed_at: string };
export type GateResult = { ok: true; record: GateRecord } | { ok: false; blockedBy: string | null; reason?: "storage" };

const DB_NAME = "observa-checkout";
const VERSION = 2;
const ATTEMPTS = "attempts";
const META = "meta";
const EFFECTS = "effects";
const COUNTER = "attempt-ref";
/** Journal size: well beyond the one attempt that can be open at a time. */
export const EFFECTS_KEPT = 20;

let opening: Promise<IDBDatabase | null> | null = null;

/** The gate's database, or null where IndexedDB is unavailable or refuses (callers fail closed). */
export function openGate(): Promise<IDBDatabase | null> {
  opening ??= new Promise<IDBDatabase | null>((resolve) => {
    try {
      if (typeof indexedDB === "undefined" || !indexedDB) return resolve(null);
      const request = indexedDB.open(DB_NAME, VERSION);
      request.onupgradeneeded = (event) => {
        const db = request.result;
        if (event.oldVersion < 1) {
          db.createObjectStore(ATTEMPTS, { keyPath: "attempt_ref" });
          db.createObjectStore(META);
        }
        if (event.oldVersion < 2) db.createObjectStore(EFFECTS, { keyPath: "attempt_ref" });
      };
      request.onsuccess = () => {
        const db = request.result;
        // A newer version in another tab: step aside so its upgrade can finish; reopen next time.
        db.onversionchange = () => {
          db.close();
          opening = null;
        };
        resolve(db);
      };
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  }).then((db) => {
    // A failure is not remembered: the next attempt to use the gate tries again.
    if (!db) opening = null;
    return db;
  });
  return opening;
}

/** Forgets the open database (tests start from a fresh one). */
export function resetGate() {
  void opening?.then((db) => db?.close());
  opening = null;
}

function randomHex(length: number) {
  return [...crypto.getRandomValues(new Uint8Array(length))].map((byte) => "0123456789ABCDEF"[byte % 16]).join("");
}

const copyLines = (lines: readonly CartLine[]) => lines.map(({ product_id, quantity }) => ({ product_id, quantity }));

/** Runs `body` in one read-write transaction; resolves its value only if the transaction committed. */
function transact<T>(db: IDBDatabase, stores: string[], failed: T, body: (tx: IDBTransaction, done: (value: T) => void) => void): Promise<T> {
  return new Promise((resolve) => {
    let value = failed;
    let tx: IDBTransaction;
    try {
      tx = db.transaction(stores, "readwrite");
    } catch {
      return resolve(failed);
    }
    tx.oncomplete = () => resolve(value);
    tx.onerror = () => resolve(failed);
    tx.onabort = () => resolve(failed);
    try {
      body(tx, (next) => {
        value = next;
      });
    } catch {
      try {
        tx.abort();
      } catch {
        // Already finished.
      }
      resolve(failed);
    }
  });
}

/** Check, allocate and store in one transaction. Never resolves ok unless the write committed. */
export function gateBegin(
  db: IDBDatabase,
  { customer_id, lines, created_at }: { customer_id: string; lines: readonly CartLine[]; created_at: string },
): Promise<GateResult> {
  return transact<GateResult>(db, [ATTEMPTS, META], { ok: false, blockedBy: null, reason: "storage" }, (tx, done) => {
    const attempts = tx.objectStore(ATTEMPTS);
    const meta = tx.objectStore(META);
    const all = attempts.getAll();
    all.onsuccess = () => {
      const stored = all.result as Partial<GateRecord>[];
      if (stored.length > 0) {
        // Anything stored blocks, even a record this version cannot read.
        return done({ ok: false, blockedBy: typeof stored[0]?.attempt_ref === "string" ? stored[0].attempt_ref : null });
      }
      const counter = meta.get(COUNTER);
      counter.onsuccess = () => {
        const state = counter.result as { prefix?: unknown; next?: unknown } | undefined;
        let prefix = typeof state?.prefix === "string" && /^[0-9A-F]{4}$/.test(state.prefix) ? state.prefix : null;
        let next = typeof state?.next === "number" && Number.isInteger(state.next) && state.next >= 1 ? state.next : 1;
        if (!prefix || next > 9999) {
          let fresh = randomHex(4);
          while (fresh === prefix) fresh = randomHex(4);
          prefix = fresh;
          next = 1;
        }
        const record: GateRecord = {
          attempt_ref: `ATT-${prefix}-${String(next).padStart(4, "0")}`,
          customer_id,
          lines: copyLines(lines),
          created_at,
          status: "submitting",
        };
        meta.put({ prefix, next: next + 1 }, COUNTER);
        attempts.add(record);
        done({ ok: true, record });
      };
    };
  });
}

/** Updates a stored attempt's status; false if it is not stored (already settled) or the write failed. */
export function gateMark(db: IDBDatabase, attemptRef: string, status: AttemptStatus): Promise<boolean> {
  return transact(db, [ATTEMPTS], false, (tx, done) => {
    const store = tx.objectStore(ATTEMPTS);
    const read = store.get(attemptRef);
    read.onsuccess = () => {
      if (!read.result) return;
      store.put({ ...(read.result as GateRecord), status });
      done(true);
    };
  });
}

/** Removes an attempt after a definite outcome that is not a confirmed order. True once committed. */
export function gateSettle(db: IDBDatabase, attemptRef: string): Promise<boolean> {
  return transact(db, [ATTEMPTS], false, (tx, done) => {
    tx.objectStore(ATTEMPTS).delete(attemptRef);
    done(true);
  });
}

/**
 * A confirmed order, in one transaction: remove the attempt and journal its cart effect. The
 * effect is journalled only while this browser still holds the attempt (or already journalled
 * it), so an old answer for an attempt settled long ago can never take units out again.
 * `ok` is true once committed; `effect` is what the cart must apply (idempotently), if anything.
 */
export function gateConfirm(db: IDBDatabase, attemptRef: string, confirmedAt: string): Promise<{ ok: boolean; effect: Effect | null }> {
  return transact<{ ok: boolean; effect: Effect | null }>(db, [ATTEMPTS, EFFECTS], { ok: false, effect: null }, (tx, done) => {
    const attempts = tx.objectStore(ATTEMPTS);
    const effects = tx.objectStore(EFFECTS);
    const held = attempts.get(attemptRef);
    held.onsuccess = () => {
      const record = held.result as GateRecord | undefined;
      const journalled = effects.get(attemptRef);
      journalled.onsuccess = () => {
        const existing = journalled.result as Effect | undefined;
        if (existing) {
          if (record) attempts.delete(attemptRef);
          return done({ ok: true, effect: existing });
        }
        if (!record) return done({ ok: true, effect: null });
        const effect: Effect = { attempt_ref: attemptRef, lines: copyLines(record.lines), confirmed_at: confirmedAt };
        attempts.delete(attemptRef);
        effects.put(effect);
        const all = effects.getAll();
        all.onsuccess = () => {
          const kept = (all.result as Effect[]).sort((a, b) => a.confirmed_at.localeCompare(b.confirmed_at));
          for (const old of kept.slice(0, Math.max(0, kept.length - EFFECTS_KEPT))) effects.delete(old.attempt_ref);
          done({ ok: true, effect });
        };
      };
    };
  });
}

/** Every stored attempt, or null if the database could not be read. */
export function gateList(db: IDBDatabase): Promise<GateRecord[] | null> {
  return readAll<GateRecord>(db, ATTEMPTS);
}

/** The journalled cart effects of recent confirmed orders. */
export async function gateEffects(db: IDBDatabase): Promise<Effect[]> {
  return (await readAll<Effect>(db, EFFECTS)) ?? [];
}

function readAll<T>(db: IDBDatabase, store: string): Promise<T[] | null> {
  return new Promise((resolve) => {
    try {
      const request = db.transaction(store, "readonly").objectStore(store).getAll();
      request.onsuccess = () => resolve(request.result as T[]);
      request.onerror = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
}
