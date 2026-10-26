// Gateway API client: the only API client the storefront bundle may import.
import type { Client } from "openapi-fetch";
import type { paths } from "./generated/gateway.ts";
import { sameOriginClient, type ClientSetup } from "./same-origin-client.ts";

export type GatewayPaths = paths;
export type GatewayClient = Client<paths>;
export type { components as GatewayComponents } from "./generated/gateway.ts";

export function createGatewayClient(setup?: ClientSetup): GatewayClient {
  return sameOriginClient<paths>(setup);
}
