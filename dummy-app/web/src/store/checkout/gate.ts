// The checkout gate: the one place that decides whether a new attempt may start.
//
// It is one IndexedDB read-write transaction that (1) refuses while any attempt is stored,
// (2) advances the reference counter and (3) stores the new attempt. IndexedDB runs read-write
// transactions on the same stores one at a time across every tab of this origin, and a later
// transaction always sees an earlier one's committed writes. localStorage gives neither
// guarantee between tabs (Chromium updates other tabs' copies asynchronously), and Web Locks
// exist only on https or localhost, so neither can serve as the gate on its own.
//
// localStorage keeps a mirror of the stored attempts for the screens and for storage events.
import type { CartLine } from "../cart/cart-store.ts";

export type GateRecord = { attempt_ref: string; lines: CartLine[]; created_at: string; status: "submitting" | "processing" | "unknown" };
export type GateResult = { ok: true; record: GateRecord } | { ok: false; blockedBy: string | null; reason?: "storage" };

const DB_NAME = "observa-checkout";
const ATTEMPTS = "attempts";
const META = "meta";
const COUNTER = "attempt-ref";

let opening: Promise<IDBDatabase | null> | null = null;

/** The gate's database, or null where IndexedDB is unavailable (then the caller falls back). */
export function openGate(): Promise<IDBDatabase | null> {
  opening ??= new Promise((resolve) => {
    try {
      if (typeof indexedDB === "undefined") return resolve(null);
      const request = indexedDB.open(DB_NAME, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore(ATTEMPTS, { keyPath: "attempt_ref" });
        request.result.createObjectStore(META);
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => resolve(null);
      request.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return opening;
}

function randomHex(length: number) {
  return [...crypto.getRandomValues(new Uint8Array(length))].map((byte) => "0123456789ABCDEF"[byte % 16]).join("");
}

/** Check, allocate and store in one transaction. Never resolves ok unless the write committed. */
export function gateBegin(db: IDBDatabase, lines: readonly CartLine[], createdAt: string): Promise<GateResult> {
  return new Promise((resolve) => {
    let outcome: GateResult = { ok: false, blockedBy: null, reason: "storage" };
    let tx: IDBTransaction;
    try {
      tx = db.transaction([ATTEMPTS, META], "readwrite");
    } catch {
      return resolve(outcome);
    }
    const attempts = tx.objectStore(ATTEMPTS);
    const meta = tx.objectStore(META);
    const all = attempts.getAll();
    all.onsuccess = () => {
      const stored = all.result as Partial<GateRecord>[];
      if (stored.length > 0) {
        // Anything stored blocks, even a record this version cannot read.
        outcome = { ok: false, blockedBy: typeof stored[0]?.attempt_ref === "string" ? stored[0].attempt_ref : null };
        return;
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
          lines: lines.map(({ product_id, quantity }) => ({ product_id, quantity })),
          created_at: createdAt,
          status: "submitting",
        };
        meta.put({ prefix, next: next + 1 }, COUNTER);
        attempts.add(record);
        outcome = { ok: true, record };
      };
    };
    // Only a committed transaction counts; an error or abort means nothing may be sent.
    tx.oncomplete = () => resolve(outcome);
    tx.onerror = () => resolve({ ok: false, blockedBy: null, reason: "storage" });
    tx.onabort = () => resolve({ ok: false, blockedBy: null, reason: "storage" });
  });
}

function write(db: IDBDatabase, change: (store: IDBObjectStore) => void): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      const tx = db.transaction(ATTEMPTS, "readwrite");
      change(tx.objectStore(ATTEMPTS));
      tx.oncomplete = () => resolve(true);
      tx.onerror = () => resolve(false);
      tx.onabort = () => resolve(false);
    } catch {
      resolve(false);
    }
  });
}

export const gateMark = (db: IDBDatabase, record: GateRecord) => write(db, (store) => store.put(record));
export const gateSettle = (db: IDBDatabase, attemptRef: string) => write(db, (store) => store.delete(attemptRef));

export function gateList(db: IDBDatabase): Promise<GateRecord[]> {
  return new Promise((resolve) => {
    try {
      const request = db.transaction(ATTEMPTS, "readonly").objectStore(ATTEMPTS).getAll();
      request.onsuccess = () => resolve(request.result as GateRecord[]);
      request.onerror = () => resolve([]);
    } catch {
      resolve([]);
    }
  });
}
