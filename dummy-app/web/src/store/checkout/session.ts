// The page's one checkout flow. It outlives the checkout page, so an answer that arrives after
// the visitor has navigated away still settles the attempt and the cart correctly.
import { useSyncExternalStore } from "react";
import { cartStore } from "../cart/cart-store.ts";
import { attemptStore } from "./attempts.ts";
import { createCheckoutFlow, type CheckoutFlow, type Session } from "./flow.ts";

let shared: CheckoutFlow | null = null;

export function checkoutFlow(): CheckoutFlow {
  shared ??= createCheckoutFlow({ cart: cartStore(), attempts: attemptStore() });
  return shared;
}

/** Forgets the flow so the next use starts idle (tests). */
export function resetCheckoutFlow() {
  shared = null;
}

export function useCheckoutSession(flow: CheckoutFlow = checkoutFlow()): Session {
  return useSyncExternalStore(flow.subscribe, flow.getSession);
}
