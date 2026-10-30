// Product search: the collection filtered by display name (src/store/search/match.ts).
// Every typed word must appear in the name, in any order, ignoring case and extra spaces.
import { describe, expect, it } from "vitest";
import { PRODUCTS } from "../e2e/fixtures/gateway.ts";
import { filterProducts, normalizeQuery } from "../src/store/search/match.ts";

const ids = (query: string) => filterProducts(PRODUCTS, query).map((p) => p.product_id);

describe("normalizing a query", () => {
  it.each([
    ["  Pen   SET ", "pen set"],
    ["", ""],
    ["   ", ""],
    ["JOURNAL\t/\nNotebook", "journal / notebook"],
  ])("%j becomes %j", (raw, expected) => expect(normalizeQuery(raw)).toBe(expected));
});

describe("filtering products", () => {
  it("an empty or blank query keeps every product, in API order", () => {
    expect(ids("")).toEqual(PRODUCTS.map((p) => p.product_id));
    expect(ids("   ")).toEqual(PRODUCTS.map((p) => p.product_id));
  });

  it("matches the display name, ignoring case", () => {
    expect(ids("pen")).toEqual(["sku-003"]);
    expect(ids("PEN")).toEqual(["sku-003"]);
    expect(ids("coffee mug")).toEqual(["sku-002"]);
  });

  it("matches part of a word, anywhere in the name", () => {
    expect(ids("ournal")).toEqual(["sku-001"]);
    expect(ids("abels")).toEqual(["sku-005"]);
  });

  it("matches the artboard name, not the API's own name", () => {
    // sku-001's API name is a short database name; the shown name is "Journal / Notebook".
    expect(ids("notebook")).toEqual(["sku-001"]);
    expect(ids("art print")).toEqual(["sku-004"]);
  });

  it("every word must match, in any order", () => {
    expect(ids("notebook journal")).toEqual(["sku-001"]);
    expect(ids("journal mug")).toEqual([]);
  });

  it("several products can match, kept in API order", () => {
    // "e" is in all five names; "t" is in three of them (not Coffee Mug or Organizing Labels).
    expect(ids("e")).toEqual(["sku-001", "sku-002", "sku-003", "sku-004", "sku-005"]);
    expect(ids("t")).toEqual(["sku-001", "sku-003", "sku-004"]);
  });

  it("no match gives an empty list; punctuation in the name can be typed", () => {
    expect(ids("zzz")).toEqual([]);
    expect(ids("/")).toEqual(["sku-001", "sku-004"]);
  });

  it("a product without a display name is found by the API's name", () => {
    const unknown = { ...PRODUCTS[0], product_id: "sku-099", name: "Wall Clock" } as (typeof PRODUCTS)[number];
    expect(filterProducts([unknown], "clock").map((p) => p.product_id)).toEqual(["sku-099"]);
  });
});
