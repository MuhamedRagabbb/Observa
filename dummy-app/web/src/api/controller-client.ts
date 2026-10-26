// Fault-controller API client: operator bundle only. The storefront must never import
// this file (enforced by test/bundle-boundary.test.ts).
import type { Client } from "openapi-fetch";
import type { paths } from "./generated/controller.ts";
import { sameOriginClient, type ClientSetup } from "./same-origin-client.ts";

export type ControllerPaths = paths;
export type ControllerClient = Client<paths>;
export type { components as ControllerComponents } from "./generated/controller.ts";

export function createControllerClient(setup?: ClientSetup): ControllerClient {
  return sameOriginClient<paths>(setup);
}
