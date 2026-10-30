// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/"}
// Server-owned prices and availability: POST /api/cart/quote through the typed Gateway client.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { quoteFor } from "../e2e/fixtures/gateway.ts";
import { createGatewayClient } from "../src/api/gateway-client.ts";
import { fetchQuote, QuoteUnavailable, useCartQuote } from "../src/store/cart/quote.ts";

afterEach(() => vi.unstubAllGlobals());

const lines = [
  { product_id: "sku-002", quantity: 2 },
  { product_id: "sku-001", quantity: 1 },
];

function recording(respond: () => Promise<Response>) {
  const requests: Request[] = [];
  const fetch = vi.fn(async (request: Request) => {
    requests.push(request.clone());
    return respond();
  });
  return { client: createGatewayClient({ fetch }), requests };
}

const failure = (promise: Promise<unknown>) => promise.then(() => null, (error: unknown) => error as QuoteUnavailable);

describe("fetchQuote", () => {
  it("posts the cart lines and returns the server's quote", async () => {
    const quote = quoteFor(lines);
    const { client, requests } = recording(async () => Response.json(quote));
    expect(await fetchQuote(client, lines)).toEqual(quote);
    expect(requests[0]?.method).toBe("POST");
    expect(new URL(requests[0]?.url ?? "").pathname).toBe("/api/cart/quote");
    expect(await requests[0]?.json()).toEqual({ items: lines });
  });

  it("reports an API error by its code", async () => {
    const { client } = recording(async () =>
      Response.json({ error_code: "dependency_unavailable", message: "down" }, { status: 503 }),
    );
    const error = await failure(fetchQuote(client, lines));
    expect(error).toBeInstanceOf(QuoteUnavailable);
    expect(error?.reason).toBe("dependency_unavailable");
  });

  it("reports a network failure", async () => {
    const { client } = recording(async () => {
      throw new TypeError("Failed to fetch");
    });
    expect((await failure(fetchQuote(client, lines)))?.reason).toBe("network");
  });

  it("reports a body that is not a quote", async () => {
    const totals = { subtotal_cents: 0, unit_count: 0, unavailable_count: 0, can_checkout: true };
    for (const body of [[], { lines: "x" }, { lines: [], subtotal_cents: "12" }, { ...totals, lines: [null] }, { ...totals, lines: [{ product_id: 7 }] }]) {
      const { client } = recording(async () => Response.json(body));
      expect((await failure(fetchQuote(client, lines)))?.reason).toBe("invalid_response");
    }
  });
});

describe("useCartQuote", () => {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>
  );

  it("sends no request for an empty cart", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const { result } = renderHook(() => useCartQuote([]), { wrapper });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetch).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe("idle");
  });

  it("quotes a cart with lines", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Response.json(quoteFor(lines))));
    const { result } = renderHook(() => useCartQuote(lines), { wrapper });
    await waitFor(() => expect(result.current.data?.subtotal_cents).toBe(4800));
  });
});
