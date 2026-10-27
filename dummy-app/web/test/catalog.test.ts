// @vitest-environment node
// The storefront shows artboard names for the seeded products; price, stock and IDs always
// come from the API. Anything the map does not know falls back to the API's own name.
import { describe, expect, it } from "vitest";
import { PRODUCTS } from "../e2e/fixtures/gateway.ts";
import { displayName, productImage } from "../src/store/catalog.ts";

describe("catalog display map", () => {
  it("names each seeded product as the artboards do", () => {
    expect(PRODUCTS.map(displayName)).toEqual([
      "Journal / Notebook",
      "Coffee Mug",
      "Pen Set",
      "Limited Poster / Art Print",
      "Organizing Labels",
    ]);
  });

  it("falls back to the API name for a product it does not know", () => {
    expect(displayName({ product_id: "sku-999", name: "Desk lamp" })).toBe("Desk lamp");
  });

  it("never mistakes an inherited object key for a product", () => {
    for (const id of ["constructor", "toString", "__proto__", "hasOwnProperty"]) {
      expect(displayName({ product_id: id, name: "API name" })).toBe("API name");
      expect(productImage(id)).toEqual({ src: null, placeholder: true });
    }
  });

  it("is keyed by product ID, never by the API name", () => {
    expect(displayName({ product_id: "sku-002", name: "Renamed in the database" })).toBe("Coffee Mug");
    expect(displayName({ product_id: "sku-999", name: "Mug" })).toBe("Mug");
  });

  it("gives each seeded product its own image and an unknown product the neutral placeholder", () => {
    const images = PRODUCTS.map((product) => productImage(product.product_id));
    expect(new Set(images.map((image) => image.src)).size).toBe(PRODUCTS.length);
    for (const image of images) expect(image.placeholder).toBe(false);
    expect(productImage("sku-999")).toEqual({ src: null, placeholder: true });
  });
});
