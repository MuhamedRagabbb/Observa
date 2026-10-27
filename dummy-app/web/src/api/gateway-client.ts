// Gateway API client: the only API client the storefront bundle may import.
import type { Client, Middleware } from "openapi-fetch";
import type { paths } from "./generated/gateway.ts";
import { sameOriginClient, type ClientSetup } from "./same-origin-client.ts";

export type GatewayPaths = paths;
export type GatewayClient = Client<paths>;
export type { components as GatewayComponents } from "./generated/gateway.ts";

/**
 * A random (version 4) UUID. crypto.randomUUID exists only in secure contexts (https or
 * localhost), and the store is also opened over plain http on a LAN address, so it falls
 * back to getRandomValues, which is available everywhere.
 */
function uuid(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = ((bytes[6] as number) & 0x0f) | 0x40;
  bytes[8] = ((bytes[8] as number) & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * Tags each request with its own ID, so a browser request can be found in the backend's logs
 * and traces. The backend keeps IDs matching [a-zA-Z0-9_-]{1,100}. Never shown to the visitor.
 */
const requestIds: Middleware = {
  onRequest({ request }) {
    request.headers.set("X-Request-ID", `web-${uuid()}`);
    return request;
  },
};

export function createGatewayClient(setup?: ClientSetup): GatewayClient {
  const client = sameOriginClient<paths>(setup);
  client.use(requestIds);
  return client;
}
