// The catalogue, read from GET /api/products. Prices, stock and IDs always come from here;
// catalog.ts only adds display names and images.
import { useQuery } from "@tanstack/react-query";
import { createGatewayClient, type GatewayClient, type GatewayComponents } from "../api/gateway-client.ts";

export type Product = GatewayComponents["schemas"]["Product"];

/** The products could not be read. `reason` is the API error code, `http_<status>` or `network`. */
export class ProductsUnavailable extends Error {
  constructor(readonly reason: string) {
    super(`Products unavailable: ${reason}`);
    this.name = "ProductsUnavailable";
  }
}

export async function fetchProducts(client: GatewayClient): Promise<Product[]> {
  let result;
  try {
    result = await client.GET("/api/products");
  } catch {
    throw new ProductsUnavailable("network");
  }
  const { data, error, response } = result;
  if (data !== undefined) {
    // The types promise a list; a proxy or misrouted answer must not crash the page.
    if (!Array.isArray(data)) throw new ProductsUnavailable("invalid_response");
    return data;
  }
  const code = typeof error === "object" && error !== null && "error_code" in error ? error.error_code : null;
  throw new ProductsUnavailable(typeof code === "string" ? code : `http_${response.status}`);
}

/** The catalogue for the storefront. Reading is safe to repeat, so "Try again" simply refetches. */
export function useProducts() {
  return useQuery({
    queryKey: ["products"],
    // A client per call: it binds fetch when created, which keeps tests' fetch stubs effective.
    queryFn: () => fetchProducts(createGatewayClient()),
    // No stale time: returning to the tab rereads stock. A failed background read keeps the
    // products already shown (see Collection).
  });
}
