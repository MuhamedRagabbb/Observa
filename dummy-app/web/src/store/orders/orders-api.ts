// My Orders data: GET /api/orders?customer_id=&limit=50 (the backend returns at most 50, newest
// first, and has no older pages), merged with this browser's unconfirmed attempts. When the list
// already has a definite answer for an attempt this browser holds, the attempt is settled through
// the checkout flow (flow.reconcile), so a confirmed order's cart effect still happens once.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";
import { createGatewayClient } from "../../api/gateway-client.ts";
import { customerId, useAttemptsSynced, useUnconfirmedAttempts } from "../checkout/attempts.ts";
import { isCheckoutResult, type CheckoutResult } from "../checkout/outcome.ts";
import { checkoutFlow } from "../checkout/session.ts";
import { classifyStatus } from "../checkout/status.ts";
import { mergeOrders, rowFromResult, type OrderRow } from "./merge.ts";

export const ORDERS_LIMIT = 50;

/** The orders could not be read: the API error code, http_<status>, invalid_response or network. */
export class OrdersUnavailable extends Error {
  constructor(readonly reason: string) {
    super(`Orders unavailable: ${reason}`);
    this.name = "OrdersUnavailable";
  }
}

async function fetchOrders(customer: string): Promise<CheckoutResult[]> {
  let result;
  try {
    result = await createGatewayClient().GET("/api/orders", { params: { query: { customer_id: customer, limit: ORDERS_LIMIT } } });
  } catch {
    throw new OrdersUnavailable("network");
  }
  const { data, error, response } = result;
  if (data === undefined) {
    const code = typeof error === "object" && error !== null && "error_code" in error ? error.error_code : null;
    throw new OrdersUnavailable(typeof code === "string" ? code : `http_${response.status}`);
  }
  if (!Array.isArray(data)) throw new OrdersUnavailable("invalid_response");
  // An entry this app cannot read is left out rather than shown wrong.
  return (data as unknown[]).filter(isCheckoutResult);
}

/** The customer's orders and this browser's unconfirmed attempts, as one list of rows. */
export function useOrderRows() {
  // The customer ID is read once per page: it names whose orders these are.
  const customer = useMemo(() => customerId(), []);
  const query = useQuery({ queryKey: ["orders", customer], queryFn: () => fetchOrders(customer) });
  const local = useUnconfirmedAttempts();
  const synced = useAttemptsSynced();
  const server = query.data;
  const rows = useMemo(() => mergeOrders(server ?? [], local), [server, local]);

  // A definite answer in the list for an attempt still held here: settle it (read-only, no request).
  useEffect(() => {
    if (!server) return;
    const flow = checkoutFlow();
    for (const record of local) {
      const entry = server.find((result) => result.attempt_ref === record.attempt_ref);
      if (entry) void flow.reconcile(record, entry, customer);
    }
  }, [server, local, customer]);

  // An attempt settled (here, by Check status, or in another tab): reread the list so its row
  // shows the server's answer.
  const refetch = query.refetch;
  const previous = useRef(local);
  useEffect(() => {
    const gone = previous.current.some((record) => !local.some((a) => a.attempt_ref === record.attempt_ref));
    previous.current = local;
    if (gone) void refetch();
  }, [local, refetch]);

  return { rows, query, synced, customer, limited: (server?.length ?? 0) >= ORDERS_LIMIT };
}

/** A single attempt by reference, read-only (GET /api/attempts/{ref}); never resolves. */
export function useAttemptLookup(attemptRef: string | null, customer: string) {
  return useQuery({
    queryKey: ["attempt", attemptRef, customer],
    enabled: attemptRef !== null,
    retry: false,
    queryFn: async (): Promise<OrderRow | null> => {
      let response;
      try {
        const { response: r, data, error } = await createGatewayClient().GET("/api/attempts/{attempt_ref}", {
          params: { path: { attempt_ref: attemptRef ?? "" }, query: { customer_id: customer } },
        });
        response = { status: r.status, body: data ?? error };
      } catch {
        response = { networkError: true as const };
      }
      const answer = classifyStatus(response, attemptRef ?? "");
      if (answer.kind === "not_found") return null;
      if (answer.kind !== "definite") throw new OrdersUnavailable("lookup_failed");
      return rowFromResult(answer.result, null);
    },
  });
}
