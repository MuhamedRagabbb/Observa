// Display names for the storefront, keyed by product ID. The database keeps its own short
// names; the approved artboards use these. Price, stock and IDs always come from the API.
import { PRODUCT_IMAGES } from "./assets/manifest.ts";

const DISPLAY_NAMES: Readonly<Record<string, string>> = {
  "sku-001": "Journal / Notebook",
  "sku-002": "Coffee Mug",
  "sku-003": "Pen Set",
  "sku-004": "Limited Poster / Art Print",
  "sku-005": "Organizing Labels",
};

/** The artboard name for a known product, otherwise the API's own name. */
export function displayName(product: { product_id: string; name: string }): string {
  return Object.hasOwn(DISPLAY_NAMES, product.product_id) ? (DISPLAY_NAMES[product.product_id] as string) : product.name;
}

export type ProductImageSource = { src: string; placeholder: false } | { src: null; placeholder: true };

/** The product's image file, or the neutral placeholder tile for a product without one. */
export function productImage(productId: string): ProductImageSource {
  const src = Object.hasOwn(PRODUCT_IMAGES, productId) ? PRODUCT_IMAGES[productId] : undefined;
  return typeof src === "string" ? { src, placeholder: false } : { src: null, placeholder: true };
}
