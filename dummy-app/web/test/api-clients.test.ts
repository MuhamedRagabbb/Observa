// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/orders/ATT-1"}
// Each bundle talks only to the origin that served it: the Gateway serves the store,
// the controller serves the operator. API paths are absolute, never under /ui/next/.
import { describe, expect, it, vi } from "vitest";
import { createControllerClient } from "../src/api/controller-client.ts";
import { createGatewayClient } from "../src/api/gateway-client.ts";

function recordingFetch(body: unknown, status = 200) {
  const requests: Request[] = [];
  const fetch = vi.fn(async (request: Request) => {
    requests.push(request);
    return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  });
  return { fetch, requests };
}

describe("typed API clients", () => {
  it("the Gateway client reads from the page's own origin", async () => {
    const product = { product_id: "p-1", name: "Mug", price_cents: 1800, stock: 3 };
    const { fetch, requests } = recordingFetch([product]);
    const { data } = await createGatewayClient({ fetch }).GET("/api/products");
    expect(requests.map((r) => [r.method, r.url])).toEqual([["GET", "http://127.0.0.1:8000/api/products"]]);
    expect(data).toEqual([product]);
  });

  it("the controller client posts JSON to the page's own origin", async () => {
    const { fetch, requests } = recordingFetch({ active: null });
    await createControllerClient({ fetch }).POST("/faults/activate", {
      body: { scenario: "payments_errors", duration_seconds: 60 },
    });
    const [request] = requests;
    expect([request?.method, request?.url]).toEqual(["POST", "http://127.0.0.1:8000/faults/activate"]);
    expect(request?.headers.get("Content-Type")).toBe("application/json");
    expect(await request?.json()).toEqual({ scenario: "payments_errors", duration_seconds: 60 });
  });

  it("returns API errors as data instead of throwing", async () => {
    const { fetch } = recordingFetch({ error_code: "validation_error", message: "Bad cart" }, 422);
    const { data, error, response } = await createGatewayClient({ fetch }).POST("/api/cart/quote", {
      body: { items: [] },
    });
    expect(response.status).toBe(422);
    expect(data).toBeUndefined();
    expect(error).toMatchObject({ error_code: "validation_error" });
  });
});
