// Browser-side checkout bookkeeping (approved P4 plan):
// - one anonymous customer ID per browser, web-<22 base36>; no personal data;
// - attempt references ATT-PPPP-NNNN (the Idempotency-Key): a random 16-bit prefix and a
//   persisted counter, allocated under a Web Lock so two tabs never get the same one;
// - unconfirmed attempts: written before the checkout POST, kept until a definite outcome,
//   shared across tabs. They hold the reference, cart lines and time, never contact details.
import { useSyncExternalStore } from "react";
import type { CartLine } from "../cart/cart-store.ts";
import { gateBegin, gateList, gateMark, gateSettle, openGate } from "./gate.ts";

export const CUSTOMER_KEY = "observa.customer";
export const REFERENCE_KEY = "observa.attempt-ref";
export const ATTEMPTS_KEY = "observa.attempts";
const VERSION = 1;
const CUSTOMER = /^web-[0-9a-z]{22}$/;
const ATTEMPT_REF = /^ATT-[0-9A-F]{4}-[0-9]{4}$/; // services/shared/checkout.py
const PRODUCT_ID = /^sku-[0-9]{3}$/;

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;
export type LockManager = { request<T>(name: string, callback: () => Promise<T> | T): Promise<T> };

function randomString(length: number, alphabet: string) {
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return [...bytes].map((byte) => alphabet[byte % alphabet.length]).join("");
}

function readJson(storage: Storage, key: string): unknown {
  try {
    const raw = storage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeJson(storage: Storage, key: string, value: unknown) {
  try {
    storage.setItem(key, JSON.stringify(value));
  } catch {
    // Blocked storage: the page still works, but nothing survives a reload (documented, U9).
  }
}

/** This browser's anonymous customer ID, created on first use. */
export function customerId(storage: Storage = window.localStorage): string {
  let stored: string | null = null;
  try {
    stored = storage.getItem(CUSTOMER_KEY);
  } catch {
    stored = null;
  }
  if (stored && CUSTOMER.test(stored)) return stored;
  const id = `web-${randomString(22, "0123456789abcdefghijklmnopqrstuvwxyz")}`;
  try {
    storage.setItem(CUSTOMER_KEY, id);
  } catch {
    // Without storage the ID lasts for this page only.
  }
  return id;
}

/** In-tab fallback when the browser has no Web Locks: still serialises this tab's requests. */
let localTail: Promise<unknown> = Promise.resolve();
const localLocks: LockManager = {
  request(_name, callback) {
    const run = localTail.then(() => callback());
    localTail = run.catch(() => undefined);
    return run;
  },
};

function browserLocks(): LockManager {
  const locks = (navigator as Navigator & { locks?: LockManager }).locks;
  return locks ?? localLocks;
}

/**
 * The next attempt reference. Allocation and the counter's write happen inside one lock that
 * every tab of this origin shares, so a reference is never handed out twice. When the counter
 * passes 9999 a new random prefix starts at 0001 instead of wrapping.
 */
export function allocateReference(storage: Storage = window.localStorage, locks: LockManager = browserLocks()) {
  return locks.request(LOCK, () => nextReference(storage));
}

/** One lock for references and attempt starts, so counter writes can never interleave. */
const LOCK = "observa-checkout";

/** The counter step itself; callers must hold LOCK. */
function nextReference(storage: Storage): string {
  {
    const state = readJson(storage, REFERENCE_KEY) as { prefix?: unknown; next?: unknown } | null;
    let prefix = typeof state?.prefix === "string" && /^[0-9A-F]{4}$/.test(state.prefix) ? state.prefix : null;
    let next = typeof state?.next === "number" && Number.isInteger(state.next) && state.next >= 1 ? state.next : 1;
    if (!prefix || next > 9999) {
      let fresh = randomString(4, "0123456789ABCDEF");
      while (fresh === prefix) fresh = randomString(4, "0123456789ABCDEF");
      prefix = fresh;
      next = 1;
    }
    writeJson(storage, REFERENCE_KEY, { prefix, next: next + 1 });
    return `ATT-${prefix}-${String(next).padStart(4, "0")}`;
  }
}

export type AttemptStatus = "submitting" | "processing" | "unknown";
export type AttemptRecord = { attempt_ref: string; lines: CartLine[]; created_at: string; status: AttemptStatus };

const STATUSES: readonly AttemptStatus[] = ["submitting", "processing", "unknown"];

function isRecord(value: unknown): value is AttemptRecord {
  if (typeof value !== "object" || value === null) return false;
  const record = value as Record<string, unknown>;
  return (
    typeof record.attempt_ref === "string" &&
    ATTEMPT_REF.test(record.attempt_ref) &&
    typeof record.created_at === "string" &&
    STATUSES.includes(record.status as AttemptStatus) &&
    Array.isArray(record.lines) &&
    record.lines.every(
      (line: unknown) =>
        typeof line === "object" &&
        line !== null &&
        PRODUCT_ID.test(String((line as CartLine).product_id)) &&
        Number.isInteger((line as CartLine).quantity),
    )
  );
}

function readAttempts(storage: Storage): AttemptRecord[] {
  const stored = readJson(storage, ATTEMPTS_KEY) as { version?: unknown; attempts?: unknown } | null;
  if (!stored || stored.version !== VERSION || !Array.isArray(stored.attempts)) return [];
  // Only the known fields are kept, so nothing else (such as contact data) can ride along.
  return stored.attempts.filter(isRecord).map(({ attempt_ref, lines, created_at, status }) => ({
    attempt_ref,
    lines: lines.map(({ product_id, quantity }) => ({ product_id, quantity })),
    created_at,
    status,
  }));
}

/** Unconfirmed checkout attempts for this browser, shared across tabs. */
export function createAttemptStore(storage: Storage = window.localStorage) {
  let attempts = readAttempts(storage);
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((listener) => listener());
  const commit = (next: AttemptRecord[]) => {
    attempts = next;
    writeJson(storage, ATTEMPTS_KEY, { version: VERSION, attempts });
    emit();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== ATTEMPTS_KEY && event.key !== null) return;
    attempts = readAttempts(storage);
    emit();
  };
  // What storage holds, plus what this tab knows: if a write failed, the record is not forgotten.
  const current = () => union(attempts, readAttempts(storage));
  return {
    unconfirmed: () => attempts,
    /** Rereads storage (after a write made outside this store, such as beginAttempt). */
    refresh() {
      attempts = readAttempts(storage);
      emit();
    },
    subscribe(listener: () => void) {
      if (listeners.size === 0) window.addEventListener("storage", onStorage);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) window.removeEventListener("storage", onStorage);
      };
    },
    /** Written before the POST is sent: the server may record the attempt even if no answer arrives. */
    record({ attempt_ref, lines, created_at }: Omit<AttemptRecord, "status">) {
      const record: AttemptRecord = { attempt_ref, lines: lines.map(({ product_id, quantity }) => ({ product_id, quantity })), created_at, status: "submitting" };
      commit(union(current(), [record]));
    },
    mark(attemptRef: string, status: AttemptStatus) {
      const next = current().map((a) => (a.attempt_ref === attemptRef ? { ...a, status } : a));
      commit(next);
      const record = next.find((a) => a.attempt_ref === attemptRef);
      if (record) void openGate().then((db) => db && gateMark(db, record));
    },
    /** Only a definite outcome may remove a record. */
    settle(attemptRef: string) {
      commit(current().filter((a) => a.attempt_ref !== attemptRef));
      void openGate().then((db) => db && gateSettle(db, attemptRef));
    },
    /** Brings back any attempt the gate holds but the mirror lost (cleared or failed writes). */
    async syncFromGate() {
      const db = await openGate();
      if (!db) return;
      const held = (await gateList(db)).filter(isRecord);
      const missing = held.filter((record) => !attempts.some((a) => a.attempt_ref === record.attempt_ref));
      if (missing.length > 0) commit(union(current(), missing));
    },
  };
}

/** Both lists, one entry per reference; entries in `extra` win. */
function union(base: AttemptRecord[], extra: AttemptRecord[]): AttemptRecord[] {
  const refs = new Set(extra.map((a) => a.attempt_ref));
  return [...base.filter((a) => !refs.has(a.attempt_ref)), ...extra];
}

export type AttemptStore = ReturnType<typeof createAttemptStore>;

export type BeginResult = { ok: true; attemptRef: string } | { ok: false; blockedBy: string | null; reason?: "storage" };

/**
 * Starts a checkout attempt: refuse while any attempt is unconfirmed (its outcome could be a
 * placed order), otherwise allocate a reference and store the attempt. Only an ok result may
 * be followed by the POST, and ok is returned only once the record is durably stored.
 *
 * The decision is made by the IndexedDB gate (gate.ts), atomic across tabs. Where IndexedDB
 * is unavailable it falls back to a Web Lock (or an in-tab queue) over localStorage, and reads
 * the record back before saying ok.
 */
export async function beginAttempt(
  lines: readonly CartLine[],
  {
    storage = window.localStorage,
    locks = browserLocks(),
    now = () => new Date().toISOString(),
  }: { storage?: Storage; locks?: LockManager; now?: () => string } = {},
): Promise<BeginResult> {
  if (lines.length === 0) return { ok: false, blockedBy: null };
  const db = await openGate();
  if (db) {
    const result = await gateBegin(db, lines, now());
    if (!result.ok) {
      // The blocking attempt may be missing from this tab's mirror: bring it back for the screens.
      await shared?.syncFromGate();
      return result;
    }
    // Mirror for the screens and other tabs; the gate already holds the record durably.
    writeJson(storage, ATTEMPTS_KEY, { version: VERSION, attempts: union(readAttempts(storage), [result.record]) });
    shared?.refresh();
    return { ok: true, attemptRef: result.record.attempt_ref };
  }
  return locks.request(LOCK, (): BeginResult => {
    const pending = readAttempts(storage)[0];
    if (pending) return { ok: false, blockedBy: pending.attempt_ref };
    const attemptRef = nextReference(storage);
    const record: AttemptRecord = {
      attempt_ref: attemptRef,
      lines: lines.map(({ product_id, quantity }) => ({ product_id, quantity })),
      created_at: now(),
      status: "submitting",
    };
    writeJson(storage, ATTEMPTS_KEY, { version: VERSION, attempts: union(readAttempts(storage), [record]) });
    // Never send what was not saved: a full or blocked storage fails this check.
    if (!readAttempts(storage).some((a) => a.attempt_ref === attemptRef)) return { ok: false, blockedBy: null, reason: "storage" };
    shared?.refresh();
    return { ok: true, attemptRef };
  });
}

let shared: AttemptStore | null = null;

export function attemptStore(): AttemptStore {
  if (!shared) {
    shared = createAttemptStore();
    void shared.syncFromGate();
  }
  return shared;
}

/** Forgets the page's attempt store so the next use rereads storage (tests). */
export function resetAttemptStore() {
  shared = null;
}

export function useUnconfirmedAttempts(store: AttemptStore = attemptStore()): AttemptRecord[] {
  return useSyncExternalStore(store.subscribe, store.unconfirmed);
}
