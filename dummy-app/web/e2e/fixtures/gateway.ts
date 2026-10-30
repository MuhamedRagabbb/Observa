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

export type CartQuote = components["schemas"]["CartQuote"];
export type QuoteLine = components["schemas"]["QuoteLine"];

/**
 * The quote the Gateway would return for these lines against this catalogue: the same rules as
 * build_quote in services/shared/checkout.py (lines sorted by product ID, unavailable lines
 * excluded from the subtotal and unit count).
 */
export function quoteFor(items: { product_id: string; quantity: number }[], products: Product[] = PRODUCTS): CartQuote {
  const catalog = new Map(products.map((product) => [product.product_id, product]));
  const lines: QuoteLine[] = [...items]
    .sort((a, b) => a.product_id.localeCompare(b.product_id))
    .map(({ product_id, quantity }) => {
      const product = catalog.get(product_id);
      if (!product) {
        return { product_id, name: null, quantity, unit_price_cents: null, line_total_cents: null, available: false, unavailable_reason: "unknown_product" };
      }
      const reason = product.stock <= 0 ? "out_of_stock" : product.stock < quantity ? "insufficient_stock" : null;
      return {
        product_id,
        name: product.name,
        quantity,
        unit_price_cents: product.price_cents,
        line_total_cents: product.price_cents * quantity,
        available: reason === null,
        unavailable_reason: reason,
      };
    });
  const available = lines.filter((line) => line.available);
  return {
    lines,
    subtotal_cents: available.reduce((sum, line) => sum + (line.line_total_cents ?? 0), 0),
    unit_count: available.reduce((sum, line) => sum + line.quantity, 0),
    unavailable_count: lines.length - available.length,
    can_checkout: lines.length > 0 && available.length === lines.length,
  };
}
