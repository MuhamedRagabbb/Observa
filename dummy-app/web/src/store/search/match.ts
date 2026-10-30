// Product search: the collection filtered by the name shown on screen (the display map's name,
// or the API's own name for a product the map does not know). Nothing is sent anywhere.
import { displayName } from "../catalog.ts";
import type { Product } from "../products.ts";

/** Lower case, with runs of whitespace (including newlines and tabs) reduced to one space. */
export function normalizeQuery(raw: string): string {
  return raw.toLocaleLowerCase("en-US").replace(/\s+/gu, " ").trim();
}

/** The products whose shown name contains every word typed, in any order; API order is kept. */
export function filterProducts<T extends Pick<Product, "product_id" | "name">>(products: readonly T[], query: string): T[] {
  const words = normalizeQuery(query).split(" ").filter(Boolean);
  if (words.length === 0) return [...products];
  return products.filter((product) => {
    const name = normalizeQuery(displayName(product));
    return words.every((word) => name.includes(word));
  });
}
