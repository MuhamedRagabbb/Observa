// Server-owned prices and availability for the cart: POST /api/cart/quote. The quote reserves
// nothing, so asking again (Try again, a quantity change) is always safe.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { createGatewayClient, type GatewayClient, type GatewayComponents } from "../../api/gateway-client.ts";
import type { CartLine } from "./cart-store.ts";

export type CartQuote = GatewayComponents["schemas"]["CartQuote"];
export type QuoteLine = GatewayComponents["schemas"]["QuoteLine"];

/** The quote could not be read. `reason` is the API error code, `http_<status>`, `network` or `invalid_response`. */
export class QuoteUnavailable extends Error {
  constructor(readonly reason: string) {
    super(`Cart quote unavailable: ${reason}`);
    this.name = "QuoteUnavailable";
  }
}

const isQuoteLine = (value: unknown) => {
  if (typeof value !== "object" || value === null) return false;
  const line = value as Record<string, unknown>;
  return typeof line.product_id === "string" && typeof line.quantity === "number" && typeof line.available === "boolean";
};

const isQuote = (value: unknown): value is CartQuote => {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const quote = value as Record<string, unknown>;
  return (
    Array.isArray(quote.lines) &&
    quote.lines.every(isQuoteLine) &&
    typeof quote.subtotal_cents === "number" &&
    typeof quote.unit_count === "number" &&
    typeof quote.unavailable_count === "number" &&
    typeof quote.can_checkout === "boolean"
  );
};

export async function fetchQuote(client: GatewayClient, lines: readonly CartLine[]): Promise<CartQuote> {
  let result;
  try {
    result = await client.POST("/api/cart/quote", { body: { items: [...lines] } });
  } catch {
    throw new QuoteUnavailable("network");
  }
  const { data, error, response } = result;
  if (data !== undefined) {
    if (!isQuote(data)) throw new QuoteUnavailable("invalid_response");
    return data;
  }
  const code = typeof error === "object" && error !== null && "error_code" in error ? error.error_code : null;
  throw new QuoteUnavailable(typeof code === "string" ? code : `http_${response.status}`);
}

/**
 * The quote for the current lines, refreshed whenever they change. While a new quote loads the
 * previous one stays on screen (marked busy), so a quantity change does not blank the cart.
 * Nothing is requested for an empty cart.
 */
export function useCartQuote(lines: readonly CartLine[]) {
  return useQuery({
    queryKey: ["cart-quote", lines],
    queryFn: () => fetchQuote(createGatewayClient(), lines),
    enabled: lines.length > 0,
    placeholderData: keepPreviousData,
  });
}
