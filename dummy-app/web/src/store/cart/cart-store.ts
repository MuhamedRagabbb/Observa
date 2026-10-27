// The browser-side cart: which products and how many, nothing else. Prices and availability
// always come from POST /api/cart/quote. Limits match the backend (services/shared/checkout.py:
// CheckoutItem and CartLines): IDs sku-NNN, 1-10 units per product, at most 10 lines.
import { useSyncExternalStore } from "react";

export type CartLine = { product_id: string; quantity: number };
export type Refusal = "max_quantity" | "max_lines" | "invalid_product";

export const MAX_QUANTITY = 10;
export const MAX_LINES = 10;
export const CART_STORAGE_KEY = "observa.cart";
const VERSION = 1;
const PRODUCT_ID = /^sku-[0-9]{3}$/;

const clamp = (quantity: number) => Math.min(MAX_QUANTITY, Math.max(1, quantity));

/** One more unit of a product, or the reason it cannot be added. */
export function addItem(lines: CartLine[], productId: string): { lines: CartLine[]; refused?: Refusal } {
  if (!PRODUCT_ID.test(productId)) return { lines, refused: "invalid_product" };
  const existing = lines.find((line) => line.product_id === productId);
  if (existing) {
    if (existing.quantity >= MAX_QUANTITY) return { lines, refused: "max_quantity" };
    return { lines: setQuantity(lines, productId, existing.quantity + 1) };
  }
  if (lines.length >= MAX_LINES) return { lines, refused: "max_lines" };
  return { lines: [...lines, { product_id: productId, quantity: 1 }] };
}

/** A new quantity for one line, kept within 1-10. An unknown product or the same quantity changes nothing. */
export function setQuantity(lines: CartLine[], productId: string, quantity: number): CartLine[] {
  const current = lines.find((line) => line.product_id === productId);
  if (!current || current.quantity === clamp(quantity)) return lines;
  return lines.map((line) => (line.product_id === productId ? { ...line, quantity: clamp(quantity) } : line));
}

/** Takes ordered units out of the cart; lines that reach zero go. Units added since stay. */
export function subtractLines(lines: CartLine[], ordered: readonly CartLine[]): CartLine[] {
  const taken = new Map(ordered.map((line) => [line.product_id, line.quantity]));
  return lines
    .map((line) => ({ ...line, quantity: line.quantity - (taken.get(line.product_id) ?? 0) }))
    .filter((line) => line.quantity > 0);
}

export function removeItems(lines: CartLine[], productIds: readonly string[]): CartLine[] {
  return lines.filter((line) => !productIds.includes(line.product_id));
}

/**
 * A stored cart, checked line by line: another version, bad JSON, bad IDs or non-integer
 * quantities are dropped, duplicates merged and the limits applied. Never throws.
 */
export function parseStoredCart(raw: string | null): CartLine[] {
  let parsed: unknown;
  try {
    parsed = raw ? JSON.parse(raw) : null;
  } catch {
    return [];
  }
  if (typeof parsed !== "object" || parsed === null || !("version" in parsed) || parsed.version !== VERSION) return [];
  const stored = "lines" in parsed && Array.isArray(parsed.lines) ? (parsed.lines as unknown[]) : [];
  const merged = new Map<string, number>();
  for (const item of stored) {
    if (typeof item !== "object" || item === null) continue;
    const { product_id: id, quantity } = item as Record<string, unknown>;
    if (typeof id !== "string" || !PRODUCT_ID.test(id)) continue;
    if (typeof quantity !== "number" || !Number.isInteger(quantity) || quantity < 1) continue;
    merged.set(id, (merged.get(id) ?? 0) + quantity);
  }
  return [...merged].slice(0, MAX_LINES).map(([product_id, quantity]) => ({ product_id, quantity: clamp(quantity) }));
}

type Storage = Pick<globalThis.Storage, "getItem" | "setItem">;

function read(storage: Storage): CartLine[] {
  try {
    return parseStoredCart(storage.getItem(CART_STORAGE_KEY));
  } catch {
    return [];
  }
}

/**
 * The cart for this browser, saved in localStorage and kept in step with other tabs through
 * the storage event. Works in memory when storage is blocked.
 */
export function createCartStore(storage: Storage = window.localStorage) {
  let lines = read(storage);
  const listeners = new Set<() => void>();
  const emit = () => listeners.forEach((listener) => listener());
  const commit = (next: CartLine[]) => {
    if (next === lines) return;
    lines = next;
    try {
      storage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: VERSION, lines }));
    } catch {
      // Storage blocked or full: the cart still works for this page.
    }
    emit();
  };
  const onStorage = (event: StorageEvent) => {
    // key null: another tab cleared all storage.
    if (event.key !== CART_STORAGE_KEY && event.key !== null) return;
    lines = read(storage);
    emit();
  };

  return {
    getLines: () => lines,
    subscribe(listener: () => void) {
      if (listeners.size === 0) window.addEventListener("storage", onStorage);
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) window.removeEventListener("storage", onStorage);
      };
    },
    /** Adds one unit; returns the refusal, if any. */
    add(productId: string): Refusal | undefined {
      const result = addItem(lines, productId);
      commit(result.lines);
      return result.refused;
    },
    setQuantity: (productId: string, quantity: number) => commit(setQuantity(lines, productId, quantity)),
    remove: (productIds: readonly string[]) => commit(removeItems(lines, productIds)),
    /** After a confirmed order: removes exactly the ordered units. */
    subtract: (ordered: readonly CartLine[]) => commit(subtractLines(lines, ordered)),
  };
}

export type CartStore = ReturnType<typeof createCartStore>;

let shared: CartStore | null = null;

/** Forgets the page's cart store so the next use rereads storage (tests start from a known cart). */
export function resetCartStore() {
  shared = null;
}

/** The page's one cart store, created on first use. */
export function cartStore(): CartStore {
  shared ??= createCartStore();
  return shared;
}

/** The current cart lines; re-renders on any change, from this tab or another. */
export function useCartLines(store: CartStore = cartStore()): CartLine[] {
  return useSyncExternalStore(store.subscribe, store.getLines);
}
