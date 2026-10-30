// @vitest-environment node
// Mock data must look like the real system, not like the artboards' sample data
// (audit item 5): the product fixtures are the seeded catalogue.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { PRODUCTS, STORE_UI_CONFIG } from "../e2e/fixtures/gateway.ts";
import { parseUiConfig } from "../src/shared/ui-config.ts";

const seed = readFileSync(join(import.meta.dirname, "..", "..", "scripts", "init-db.sql"), "utf8");

describe("mock fixtures", () => {
  it("are the products seeded by scripts/init-db.sql, in order", () => {
    const rows = [...seed.matchAll(/\('(sku-\d{3})','([^']+)',(\d+),(\d+)\)/g)].map(([, id, name, price, stock]) => ({
      product_id: id,
      name,
      price_cents: Number(price),
      stock: Number(stock),
    }));
    expect(rows).toHaveLength(5);
    expect(PRODUCTS).toEqual(rows);
  });

  it("include an out-of-stock product for the sold-out states", () => {
    expect(PRODUCTS.filter((p) => p.stock === 0).map((p) => p.product_id)).toEqual(["sku-004"]);
  });

  it("serve a valid /ui/config that never points at the live ports", () => {
    expect(parseUiConfig(STORE_UI_CONFIG)).toEqual(STORE_UI_CONFIG);
    expect(STORE_UI_CONFIG.ports.store).not.toBe(8000);
    expect(STORE_UI_CONFIG.ports.control).not.toBe(8090);
  });
});
