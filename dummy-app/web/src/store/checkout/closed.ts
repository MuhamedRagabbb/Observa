// A reminder for an attempt that Check status found was never placed (the server fenced it).
// The server does not list such attempts (GET /api/orders only has checkouts with an order), and
// the attempt's unconfirmed record is removed once it is settled, so without this the My Orders
// row would just vanish. The note keeps it on screen as "Not placed" for a day.
// It is display only: never an order, never read by the checkout gate, so it cannot allow or
// block a send. It holds the reference, customer ID, cart lines and times, never contact details.
// Storage failures are harmless: the note then lasts for this page only.
import { useSyncExternalStore } from "react";
import type { CartLine } from "../cart/cart-store.ts";

export const CLOSED_KEY = "observa.closed";
export const CLOSED_MAX = 10;
export const CLOSED_TTL_MS = 24 * 60 * 60 * 1000;
const VERSION = 1;
const CUSTOMER = /^web-[0-9a-z]{22}$/;
const ATTEMPT_REF = /^ATT-[0-9A-F]{4}-[0-9]{4}$/;
const PRODUCT_ID = /^sku-[0-9]{3}$/;

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

export type ClosedAttempt = {
  attempt_ref: string;
  customer_id: string;
  lines: CartLine[];
  /** When the attempt was started. */
  created_at: string;
  /** When Check status found it was never placed. */
  closed_at: string;
};

const isTime = (value: unknown): value is string => typeof value === "string" && !Number.isNaN(Date.parse(value));

/** A readable note with only the known fields, or null. */
function normalise(value: unknown): ClosedAttempt | null {
  if (typeof value !== "object" || value === null) return null;
  const note = value as Record<string, unknown>;
  const lines = note.lines;
  if (
    typeof note.attempt_ref !== "string" ||
    !ATTEMPT_REF.test(note.attempt_ref) ||
    typeof note.customer_id !== "string" ||
    !CUSTOMER.test(note.customer_id) ||
    !isTime(note.created_at) ||
    !isTime(note.closed_at) ||
    !Array.isArray(lines) ||
    !lines.every(
      (line: unknown) =>
        typeof line === "object" && line !== null && PRODUCT_ID.test(String((line as CartLine).product_id)) && Number.isInteger((line as CartLine).quantity),
    )
  ) {
    return null;
  }
  return {
    attempt_ref: note.attempt_ref,
    customer_id: note.customer_id,
    lines: (lines as CartLine[]).map(({ product_id, quantity }) => ({ product_id, quantity })),
    created_at: note.created_at,
    closed_at: note.closed_at,
  };
}

export function createClosedStore(storage: Storage = window.localStorage, now: () => number = Date.now) {
  /** Readable, unexpired, newest first, at most CLOSED_MAX. */
  const tidy = (notes: ClosedAttempt[]) => {
    const seen = new Set<string>();
    return notes
      .filter((note) => now() - Date.parse(note.closed_at) < CLOSED_TTL_MS)
      .sort((a, b) => Date.parse(b.closed_at) - Date.parse(a.closed_at))
      .filter((note) => !seen.has(note.attempt_ref) && seen.add(note.attempt_ref))
      .slice(0, CLOSED_MAX);
  };
  /** What storage holds now; null when storage cannot be read at all (blocked), [] when it holds nothing usable. */
  const read = (): ClosedAttempt[] | null => {
    let stored: unknown = null;
    try {
      const raw = storage.getItem(CLOSED_KEY);
      try {
        stored = raw ? JSON.parse(raw) : null;
      } catch {
        stored = null;
      }
    } catch {
      return null;
    }
    const { version, notes } = (typeof stored === "object" && stored !== null ? stored : {}) as { version?: unknown; notes?: unknown };
    if (version !== VERSION || !Array.isArray(notes)) return [];
    return tidy(notes.map(normalise).filter((note): note is ClosedAttempt => note !== null));
  };
  const write = (notes: ClosedAttempt[]) => {
    try {
      storage.setItem(CLOSED_KEY, JSON.stringify({ version: VERSION, notes }));
    } catch {
      // Blocked storage: the note stays for this page only.
    }
  };

  let notes = read() ?? [];
  const listeners = new Set<() => void>();
  const same = (a: ClosedAttempt[], b: ClosedAttempt[]) => JSON.stringify(a) === JSON.stringify(b);
  const set = (next: ClosedAttempt[]) => {
    if (same(next, notes)) return;
    notes = next;
    listeners.forEach((listener) => listener());
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key === CLOSED_KEY || event.key === null) set(read() ?? notes);
  };

  return {
    list: () => notes,
    add({ attempt_ref, customer_id, lines, created_at }: Pick<ClosedAttempt, "attempt_ref" | "customer_id" | "lines" | "created_at">) {
      const note = normalise({ attempt_ref, customer_id, lines, created_at, closed_at: new Date(now()).toISOString() });
      if (!note) return;
      // Start from what storage holds now: another tab's note is kept and one it dismissed
      // stays gone. Only when storage cannot be read does this page's own list serve.
      const base = read() ?? notes;
      if (base.some((existing) => existing.attempt_ref === note.attempt_ref)) {
        set(base);
        return;
      }
      const next = tidy([note, ...base]);
      write(next);
      set(next);
    },
    dismiss(attemptRef: string) {
      const next = tidy((read() ?? notes).filter((note) => note.attempt_ref !== attemptRef));
      write(next);
      set(next);
    },
    subscribe(listener: () => void) {
      if (listeners.size === 0) window.addEventListener("storage", onStorage);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) window.removeEventListener("storage", onStorage);
      };
    },
  };
}

export type ClosedStore = ReturnType<typeof createClosedStore>;

let shared: ClosedStore | null = null;

export function closedStore(): ClosedStore {
  shared ??= createClosedStore();
  return shared;
}

/** Forgets the page's store so the next use rereads storage (tests). */
export function resetClosedStore() {
  shared = null;
}

export function useClosedAttempts(store: ClosedStore = closedStore()): ClosedAttempt[] {
  return useSyncExternalStore(store.subscribe, store.list);
}
