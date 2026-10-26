// Compile-time checks, run by `npm run typecheck` (not by Vitest). Each client accepts
// only its own origin's paths: an unused @ts-expect-error fails the typecheck.
import { createControllerClient } from "../../src/api/controller-client.ts";
import { createGatewayClient } from "../../src/api/gateway-client.ts";

export async function storefrontCannotReachTheController() {
  const gateway = createGatewayClient();
  // @ts-expect-error the storefront's client knows only Gateway paths
  await gateway.GET("/faults/status");
  // @ts-expect-error the storefront's client knows only Gateway paths
  await gateway.POST("/faults/activate", { body: { scenario: "payments_errors" } });
  const { data } = await gateway.GET("/api/products");
  const names: string[] | undefined = data?.map((product) => product.name);
  return names;
}

export async function operatorCannotReachTheStorefrontApi() {
  const controller = createControllerClient();
  // @ts-expect-error the operator's client knows only controller paths
  await controller.GET("/api/products");
  // @ts-expect-error unknown scenario names are rejected by the generated types
  await controller.POST("/faults/activate", { body: { scenario: "not_a_fault", duration_seconds: 60 } });
  const { data } = await controller.GET("/faults/status");
  return data?.active;
}
