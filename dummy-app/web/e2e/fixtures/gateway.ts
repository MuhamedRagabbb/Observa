// Deterministic Gateway data for mocked browser tests. Typed by the generated API types, so a
// backend contract change breaks the typecheck here instead of silently diverging.
import type { components } from "../../src/api/generated/gateway.ts";
import type { UiConfig } from "../../src/shared/ui-config.ts";

export type Product = components["schemas"]["Product"];

/** The catalogue seeded by scripts/init-db.sql (checked by test/mock-fixtures.test.ts). */
export const PRODUCTS: Product[] = [
  { product_id: "sku-001", name: "Notebook", price_cents: 1200, stock: 1000000 },
  { product_id: "sku-002", name: "Mug", price_cents: 1800, stock: 1000000 },
  { product_id: "sku-003", name: "Pen set", price_cents: 600, stock: 1000000 },
  { product_id: "sku-004", name: "Limited poster", price_cents: 900, stock: 0 },
  { product_id: "sku-005", name: "Sample labels", price_cents: 300, stock: 1000000 },
];

/** The preview project's ports, never the live ones, so links in screenshots stay isolated. */
export const STORE_UI_CONFIG: UiConfig = {
  mode: "store",
  ports: { store: 18000, control: 18090, grafana: 13000, jaeger: 26686, prometheus: 19090 },
};

export const OPERATOR_UI_CONFIG: UiConfig = { ...STORE_UI_CONFIG, mode: "control" };
