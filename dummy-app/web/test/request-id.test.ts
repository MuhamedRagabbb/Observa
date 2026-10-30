// @vitest-environment jsdom
// @vitest-environment-options {"url": "http://127.0.0.1:8000/ui/next/"}
// Every storefront API call carries its own X-Request-ID (approved P4 plan), in the form the
// backend keeps (services/shared/web.py: [a-zA-Z0-9_-]{1,100}); anything else it replaces.
import { describe, expect, it, vi } from "vitest";
import { createGatewayClient } from "../src/api/gateway-client.ts";

function recordingFetch() {
  const requests: Request[] = [];
  const fetch = vi.fn(async (request: Request) => {
    requests.push(request);
    return Response.json([]);
  });
  return { fetch, requests };
}

describe("request IDs", () => {
  it("tags each Gateway request with a fresh web- request ID the backend accepts", async () => {
    const { fetch, requests } = recordingFetch();
    const client = createGatewayClient({ fetch });
    await client.GET("/api/products");
    await client.GET("/api/products");
    const ids = requests.map((request) => request.headers.get("X-Request-ID"));
    for (const id of ids) {
      expect(id).toMatch(/^web-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
      expect(id).toMatch(/^[a-zA-Z0-9_-]{1,100}$/);
    }
    expect(new Set(ids).size).toBe(2);
  });

  it("still works over plain http on a LAN address, where crypto.randomUUID does not exist", async () => {
    // Browsers expose randomUUID only in secure contexts (https or localhost).
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    try {
      const { fetch, requests } = recordingFetch();
      const { data, error } = await createGatewayClient({ fetch }).GET("/api/products");
      expect(error).toBeUndefined();
      expect(data).toEqual([]);
      expect(requests[0]?.headers.get("X-Request-ID")).toMatch(
        /^web-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
      );
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
