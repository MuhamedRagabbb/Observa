// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/"}
// Products come from GET /api/products through the typed Gateway client. Prices are integer
// cents from the API, shown as US dollars.
import { describe, expect, it, vi } from "vitest";
import { PRODUCTS } from "../e2e/fixtures/gateway.ts";
import { createGatewayClient } from "../src/api/gateway-client.ts";
import { formatMoney } from "../src/store/money.ts";
import { fetchProducts, ProductsUnavailable } from "../src/store/products.ts";

const clientAnswering = (respond: () => Promise<Response>) => createGatewayClient({ fetch: vi.fn(respond) });

describe("formatMoney", () => {
  it("shows integer cents as US dollars", () => {
    expect(formatMoney(1200)).toBe("$12.00");
    expect(formatMoney(300)).toBe("$3.00");
    expect(formatMoney(5)).toBe("$0.05");
    expect(formatMoney(0)).toBe("$0.00");
    expect(formatMoney(123456)).toBe("$1,234.56");
  });
});

describe("fetchProducts", () => {
  it("returns the products exactly as the API sends them", async () => {
    const products = await fetchProducts(clientAnswering(async () => Response.json(PRODUCTS)));
    expect(products).toEqual(PRODUCTS);
  });

  it("reports an API error with its error code", async () => {
    const client = clientAnswering(async () =>
      Response.json({ error_code: "dependency_unavailable", message: "Inventory is down" }, { status: 503 }),
    );
    const failure = await fetchProducts(client).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ProductsUnavailable);
    expect((failure as ProductsUnavailable).reason).toBe("dependency_unavailable");
  });

  it("reports a network failure", async () => {
    const client = clientAnswering(async () => {
      throw new TypeError("Failed to fetch");
    });
    const failure = await fetchProducts(client).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ProductsUnavailable);
    expect((failure as ProductsUnavailable).reason).toBe("network");
  });

  it("reports a successful answer whose body is not a list", async () => {
    const client = clientAnswering(async () => Response.json({ mode: "store" }));
    const failure = await fetchProducts(client).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ProductsUnavailable);
    expect((failure as ProductsUnavailable).reason).toBe("invalid_response");
  });

  it("reports an answer that is not a product list", async () => {
    const client = clientAnswering(async () => new Response("<html>proxy error</html>", { status: 502 }));
    const failure = await fetchProducts(client).catch((error: unknown) => error);
    expect((failure as ProductsUnavailable).reason).toBe("http_502");
  });
});
