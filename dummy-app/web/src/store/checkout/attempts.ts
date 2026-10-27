// Browser-side checkout bookkeeping (approved P4 plan):
// - one anonymous customer ID per browser, web-<22 base36>; no personal data;
// - attempt references ATT-PPPP-NNNN (the Idempotency-Key), allocated by the IndexedDB gate;
// - unconfirmed attempts: stored in the gate before the checkout POST, kept until a definite
//   outcome, shared across tabs. They hold the reference, the customer ID they were sent with,
//   cart lines, time and status: never contact details.
// Fail closed: without a working gate nothing may be sent. localStorage only mirrors the gate
// for synchronous rendering and for other tabs' storage events; it never decides anything.
import { useSyncExternalStore } from "react";
import type { CartLine } from "../cart/cart-store.ts";
import {
  gateBegin,
  gateConfirm,
  gateEffects,
  gateList,
  gateMark,
  gateSettle,
  openGate,
  type AttemptStatus,
  type Effect,
} from "./gate.ts";

export type { AttemptStatus, Effect } from "./gate.ts";

export const CUSTOMER_KEY = "observa.customer";
export const ATTEMPTS_KEY = "observa.attempts";
const VERSION = 1;
const CUSTOMER = /^web-[0-9a-z]{22}$/;
const ATTEMPT_REF = /^ATT-[0-9A-F]{4}-[0-9]{4}$/; // services/shared/checkout.py
const PRODUCT_ID = /^sku-[0-9]{3}$/;

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

function randomString(length: number, alphabet: string) {
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return [...bytes].map((byte) => alphabet[byte % alphabet.length]).join("");
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
    // Without storage the ID lasts for this page only; each attempt keeps the ID it was sent with.
  }
  return id;
}

export type AttemptRecord = { attempt_ref: string; customer_id: string; lines: CartLine[]; created_at: string; status: AttemptStatus };

const STATUSES: readonly AttemptStatus[] = ["submitting", "processing", "unknown"];

/** A readable record, with only the known fields (so nothing else, such as contact data, rides along). */
function normalise(value: unknown, fallbackCustomer: () => string): AttemptRecord | null {
  if (typeof value !== "object" || value === null) return null;
  const record = value as Record<string, unknown>;
  const lines = record.lines;
  if (
    typeof record.attempt_ref !== "string" ||
    !ATTEMPT_REF.test(record.attempt_ref) ||
    typeof record.created_at !== "string" ||
    !STATUSES.includes(record.status as AttemptStatus) ||
    !Array.isArray(lines) ||
    !lines.every(
      (line: unknown) =>
        typeof line === "object" && line !== null && PRODUCT_ID.test(String((line as CartLine).product_id)) && Number.isInteger((line as CartLine).quantity),
    )
  ) {
    return null;
  }
  // A record from before customer IDs were stored with it was sent with this browser's ID.
  const customer = typeof record.customer_id === "string" && CUSTOMER.test(record.customer_id) ? record.customer_id : fallbackCustomer();
  return {
    attempt_ref: record.attempt_ref,
    customer_id: customer,
    lines: (lines as CartLine[]).map(({ product_id, quantity }) => ({ product_id, quantity })),
    created_at: record.created_at,
    status: record.status as AttemptStatus,
  };
}

function readMirror(storage: Storage): AttemptRecord[] {
  let stored: unknown = null;
  try {
    const raw = storage.getItem(ATTEMPTS_KEY);
    stored = raw ? JSON.parse(raw) : null;
  } catch {
    stored = null;
  }
  const { version, attempts } = (typeof stored === "object" && stored !== null ? stored : {}) as { version?: unknown; attempts?: unknown };
  if (version !== VERSION || !Array.isArray(attempts)) return [];
  return attempts.map((a: unknown) => normalise(a, () => customerId(storage))).filter((a): a is AttemptRecord => a !== null);
}

function writeMirror(storage: Storage, attempts: AttemptRecord[]) {
  try {
    const value = JSON.stringify({ version: VERSION, attempts });
    if (storage.getItem(ATTEMPTS_KEY) !== value) storage.setItem(ATTEMPTS_KEY, value);
  } catch {
    // Blocked storage: this tab still renders from memory; the gate keeps the truth.
  }
}

const sameList = (a: AttemptRecord[], b: AttemptRecord[]) => JSON.stringify(a) === JSON.stringify(b);

/**
 * Unconfirmed checkout attempts for this browser, shared across tabs. Every change goes to the
 * gate first; the list (and its localStorage mirror) is then re-read from the gate, so a tab
 * with an old copy can never bring a settled attempt back.
 */
export function createAttemptStore(storage: Storage = window.localStorage) {
  let attempts = readMirror(storage);
  /** Whether the list has been read from the gate at least once (or there is no gate to read). */
  let synced = false;
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((listener) => listener());
  const set = (next: AttemptRecord[]) => {
    if (sameList(next, attempts)) return;
    attempts = next;
    emit();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== ATTEMPTS_KEY && event.key !== null) return;
    set(readMirror(storage));
    void sync();
  };

  /** Reloads the list from the gate and refreshes the mirror. */
  async function sync() {
    const db = await openGate();
    const held = db ? await gateList(db) : null;
    if (held === null) {
      if (!synced) {
        synced = true;
        emit();
      }
      return;
    }
    const next = held.map((a) => normalise(a, () => customerId(storage))).filter((a): a is AttemptRecord => a !== null);
    next.sort((a, b) => a.created_at.localeCompare(b.created_at));
    writeMirror(storage, next);
    const first = !synced;
    synced = true;
    if (!sameList(next, attempts)) set(next);
    else if (first) emit();
  }

  async function withGate<T>(failed: T, change: (db: IDBDatabase) => Promise<T>): Promise<T> {
    const db = await openGate();
    if (!db) return failed;
    const result = await change(db);
    await sync();
    return result;
  }

  return {
    unconfirmed: () => attempts,
    isSynced: () => synced,
    sync,
    subscribe(listener: () => void) {
      if (listeners.size === 0) window.addEventListener("storage", onStorage);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) window.removeEventListener("storage", onStorage);
      };
    },
    /** Records a status that is not yet a definite outcome (processing or unknown). */
    mark: (attemptRef: string, status: AttemptStatus) => withGate(false, (db) => gateMark(db, attemptRef, status)),
    /** A definite outcome other than a confirmed order: the attempt is removed. True once committed. */
    settle: (attemptRef: string) => withGate(false, (db) => gateSettle(db, attemptRef)),
    /** A confirmed order: removes the attempt and journals its cart effect in one transaction. */
    confirm: (attemptRef: string, confirmedAt: string) =>
      withGate<{ ok: boolean; effect: Effect | null }>({ ok: false, effect: null }, (db) => gateConfirm(db, attemptRef, confirmedAt)),
    /** Journalled cart effects of recent confirmed orders (replayed on start). */
    async effects(): Promise<Effect[]> {
      const db = await openGate();
      return db ? gateEffects(db) : [];
    },
  };
}

export type AttemptStore = ReturnType<typeof createAttemptStore>;

export type BeginResult = { ok: true; attemptRef: string } | { ok: false; blockedBy: string | null; reason?: "storage" };

/**
 * Starts a checkout attempt: refuse while any attempt is unconfirmed (its outcome could be a
 * placed order), otherwise allocate a reference and store the attempt, all in one gate
 * transaction. Only an ok result may be followed by the POST, and ok is returned only once the
 * record is durably stored. Without a working gate the answer is always "storage" (fail closed).
 */
export async function beginAttempt(
  lines: readonly CartLine[],
  { customerId: customer, now = () => new Date().toISOString() }: { customerId: string; now?: () => string },
): Promise<BeginResult> {
  if (lines.length === 0) return { ok: false, blockedBy: null };
  const db = await openGate();
  if (!db) return { ok: false, blockedBy: null, reason: "storage" };
  const result = await gateBegin(db, { customer_id: customer, lines, created_at: now() });
  return result.ok ? { ok: true, attemptRef: result.record.attempt_ref } : result;
}

let shared: AttemptStore | null = null;

export function attemptStore(): AttemptStore {
  if (!shared) {
    shared = createAttemptStore();
    void shared.sync();
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

/** False until the list has been read from the gate once: until then the mirror may be behind. */
export function useAttemptsSynced(store: AttemptStore = attemptStore()): boolean {
  return useSyncExternalStore(store.subscribe, store.isSynced);
}
