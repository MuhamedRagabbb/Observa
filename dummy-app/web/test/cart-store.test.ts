// @vitest-environment jsdom
// The cart lives in the browser: lines of {product_id, quantity} with the backend's limits
// (services/shared/checkout.py: 1-10 units per product, at most 10 lines, IDs sku-NNN).
// Prices and availability never live here; the quote supplies them.
import { afterEach, describe, expect, it } from "vitest";
import { EFFECTS_KEPT } from "../src/store/checkout/gate.ts";
import { addItem, APPLIED_KEPT, CART_STORAGE_KEY, createCartStore, parseStoredCart, removeItems, setQuantity, subtractLines } from "../src/store/cart/cart-store.ts";

afterEach(() => localStorage.clear());

const line = (product_id: string, quantity = 1) => ({ product_id, quantity });

describe("cart rules", () => {
  it("adds a new product as one unit, and one more unit of a product already there", () => {
    let result = addItem([], "sku-002");
    expect(result).toEqual({ lines: [line("sku-002")] });
    result = addItem(result.lines, "sku-002");
    expect(result).toEqual({ lines: [line("sku-002", 2)] });
  });

  it("keeps the order products were added in", () => {
    const { lines } = addItem(addItem([], "sku-003").lines, "sku-001");
    expect(lines.map((l) => l.product_id)).toEqual(["sku-003", "sku-001"]);
  });

  it("refuses an 11th unit of one product", () => {
    expect(addItem([line("sku-001", 10)], "sku-001")).toEqual({ lines: [line("sku-001", 10)], refused: "max_quantity" });
  });

  it("refuses an 11th line", () => {
    const full = Array.from({ length: 10 }, (_, i) => line(`sku-${String(i + 1).padStart(3, "0")}`));
    expect(addItem(full, "sku-011")).toEqual({ lines: full, refused: "max_lines" });
    // More of a product already in a full cart is still fine.
    expect(addItem(full, "sku-001").refused).toBeUndefined();
  });

  it("refuses an ID the backend would reject", () => {
    for (const id of ["sku-1", "SKU-001", "sku-0001", "", "../sku-001"]) {
      expect(addItem([], id)).toEqual({ lines: [], refused: "invalid_product" });
    }
  });

  it("sets a quantity within 1-10 and leaves other lines alone", () => {
    const lines = [line("sku-001", 2), line("sku-002", 1)];
    expect(setQuantity(lines, "sku-001", 5)).toEqual([line("sku-001", 5), line("sku-002", 1)]);
    expect(setQuantity(lines, "sku-001", 0)).toEqual([line("sku-001", 1), line("sku-002", 1)]);
    expect(setQuantity(lines, "sku-001", 42)).toEqual([line("sku-001", 10), line("sku-002", 1)]);
    expect(setQuantity(lines, "sku-009", 3)).toBe(lines);
    expect(setQuantity(lines, "sku-001", 2)).toBe(lines);
  });

  it("subtracts ordered units, dropping lines that reach zero", () => {
    const lines = [line("sku-001", 3), line("sku-002", 1), line("sku-005", 2)];
    expect(subtractLines(lines, [line("sku-001", 1), line("sku-002", 1), line("sku-009", 4)])).toEqual([line("sku-001", 2), line("sku-005", 2)]);
  });

  it("removes the named products", () => {
    const lines = [line("sku-001"), line("sku-002"), line("sku-004")];
    expect(removeItems(lines, ["sku-002", "sku-004"])).toEqual([line("sku-001")]);
  });
});

describe("stored cart", () => {
  it("reads a valid stored cart", () => {
    expect(parseStoredCart(JSON.stringify({ version: 1, lines: [line("sku-002", 2)] }))).toEqual([line("sku-002", 2)]);
  });

  it("reads anything unusable as an empty cart", () => {
    for (const raw of [null, "", "not json", "[]", "{}", JSON.stringify({ version: 2, lines: [] })]) {
      expect(parseStoredCart(raw)).toEqual([]);
    }
  });

  it("drops bad lines, merges duplicates and keeps the limits", () => {
    const raw = JSON.stringify({
      version: 1,
      lines: [
        line("sku-001", 4),
        line("sku-001", 9),
        { product_id: "sku-002", quantity: "3" },
        { product_id: "evil", quantity: 1 },
        line("sku-003", 0),
        line("sku-005", 2.5),
        ...Array.from({ length: 12 }, (_, i) => line(`sku-1${String(i).padStart(2, "0")}`)),
      ],
    });
    const lines = parseStoredCart(raw);
    expect(lines[0]).toEqual(line("sku-001", 10));
    expect(lines.map((l) => l.product_id)).not.toContain("sku-002");
    expect(lines.map((l) => l.product_id)).not.toContain("evil");
    expect(lines.map((l) => l.product_id)).not.toContain("sku-003");
    expect(lines.map((l) => l.product_id)).not.toContain("sku-005");
    expect(lines).toHaveLength(10);
  });
});

describe("cart store", () => {
  it("persists every change and reports refusals", () => {
    const store = createCartStore();
    expect(store.add("sku-002")).toBeUndefined();
    store.add("sku-002");
    expect(JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "")).toEqual({ version: 1, lines: [line("sku-002", 2)] });
    store.setQuantity("sku-002", 10);
    expect(store.add("sku-002")).toBe("max_quantity");
    store.remove(["sku-002"]);
    expect(store.getLines()).toEqual([]);
  });

  it("starts from what an earlier visit stored", () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: [line("sku-005", 3)] }));
    expect(createCartStore().getLines()).toEqual([line("sku-005", 3)]);
  });

  it("follows changes made in another tab", () => {
    const store = createCartStore();
    const seen: number[] = [];
    const unsubscribe = store.subscribe(() => seen.push(store.getLines().length));
    const value = JSON.stringify({ version: 1, lines: [line("sku-001"), line("sku-003")] });
    localStorage.setItem(CART_STORAGE_KEY, value);
    window.dispatchEvent(new StorageEvent("storage", { key: CART_STORAGE_KEY, newValue: value }));
    window.dispatchEvent(new StorageEvent("storage", { key: "something-else", newValue: "x" }));
    expect(store.getLines()).toEqual([line("sku-001"), line("sku-003")]);
    // Another tab clearing all storage empties this cart too.
    localStorage.clear();
    window.dispatchEvent(new StorageEvent("storage", { key: null }));
    expect(store.getLines()).toEqual([]);
    expect(seen).toEqual([2, 0]);
    unsubscribe();
  });

  it("keeps a stable snapshot between changes (for useSyncExternalStore)", () => {
    const store = createCartStore();
    expect(store.getLines()).toBe(store.getLines());
  });

  it("still works when storage is unavailable (private mode, blocked site data)", () => {
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    const store = createCartStore(broken);
    store.add("sku-001");
    expect(store.getLines()).toEqual([line("sku-001")]);
  });
});

describe("a confirmed order's cart effect (applied exactly once)", () => {
  const ordered = [line("sku-001", 1), line("sku-002", 2)];
  const stored = () => JSON.parse(localStorage.getItem(CART_STORAGE_KEY) ?? "null");

  it("takes the ordered units out and records the order in the same stored value", () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: [line("sku-001", 2), line("sku-002", 2)] }));
    const cart = createCartStore();
    expect(cart.applyConfirmed("ATT-1A2B-0001", ordered)).toBe(true);
    expect(cart.getLines()).toEqual([line("sku-001", 1)]);
    expect(stored()).toEqual({ version: 1, lines: [line("sku-001", 1)], applied: ["ATT-1A2B-0001"] });
  });

  it("a second application of the same order changes nothing, even after units were added again", () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ordered }));
    const cart = createCartStore();
    cart.applyConfirmed("ATT-1A2B-0001", ordered);
    cart.add("sku-001");
    expect(cart.applyConfirmed("ATT-1A2B-0001", ordered)).toBe(false);
    expect(cart.getLines()).toEqual([line("sku-001", 1)]);
  });

  it("another tab that already applied it is respected, even if this tab has not seen its write yet", () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ordered }));
    const thisTab = createCartStore();
    const otherTab = createCartStore();
    otherTab.applyConfirmed("ATT-1A2B-0001", ordered);
    otherTab.add("sku-003");
    // No storage event has reached this tab yet: it must read the stored value, not its copy.
    expect(thisTab.applyConfirmed("ATT-1A2B-0001", ordered)).toBe(false);
    expect(stored().lines).toEqual([line("sku-003", 1)]);
  });

  it("ordinary cart edits keep the record of applied orders", () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ordered }));
    const cart = createCartStore();
    cart.applyConfirmed("ATT-1A2B-0001", ordered);
    cart.add("sku-004");
    cart.setQuantity("sku-004", 3);
    cart.remove(["sku-004"]);
    expect(stored().applied).toEqual(["ATT-1A2B-0001"]);
  });

  it(`keeps the most recent ${APPLIED_KEPT} applied orders`, () => {
    const cart = createCartStore();
    for (let i = 1; i <= APPLIED_KEPT + 3; i++) cart.applyConfirmed(`ATT-1A2B-${String(i).padStart(4, "0")}`, ordered);
    expect(stored().applied).toHaveLength(APPLIED_KEPT);
    expect(stored().applied[0]).toBe("ATT-1A2B-0004");
  });

  it("remembers at least twice as many applied orders as the crash journal holds (replays can come out of order)", () => {
    expect(APPLIED_KEPT).toBeGreaterThanOrEqual(2 * EFFECTS_KEPT);
  });

  it("reads a stored cart without the record as having applied nothing", () => {
    localStorage.setItem(CART_STORAGE_KEY, JSON.stringify({ version: 1, lines: ordered }));
    expect(createCartStore().applyConfirmed("ATT-1A2B-0001", ordered)).toBe(true);
  });
});
