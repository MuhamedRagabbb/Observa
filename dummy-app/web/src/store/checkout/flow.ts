// Placing an order, safely (approved P4 plan, checkout outcome rules):
// 1. beginAttempt: refuse while any attempt is unconfirmed; otherwise allocate a reference and
//    record the attempt (reference, lines, time) BEFORE anything is sent;
// 2. exactly one POST /api/checkout, with the reference as the Idempotency-Key; never retried;
//    a 30 s limit turns a hung request into "unknown" so the page cannot spin forever;
// 3. classifyCheckout: definite only with a matching result. A definite outcome settles the
//    record (202 marks it processing); anything else marks it unknown and keeps it;
// 4. the ordered units leave the cart only on a confirmed order (units added since stay).
// Contact details go in the request body only; the in-memory copy serves "Try again".
import { createGatewayClient } from "../../api/gateway-client.ts";
import type { ClientSetup } from "../../api/same-origin-client.ts";
import type { CartLine, CartStore } from "../cart/cart-store.ts";
import { beginAttempt, customerId, type AttemptStore, type LockManager } from "./attempts.ts";
import type { Contact } from "./contact.ts";
import { classifyCheckout, type Classification, type CheckoutResponse } from "./outcome.ts";

export const CHECKOUT_TIMEOUT_MS = 30_000;

export type PlaceResult =
  | Classification
  | { kind: "busy" }
  | { kind: "blocked"; blockedBy: string | null; reason?: "storage" };

export type Session =
  | { phase: "idle" }
  | { phase: "submitting"; attemptRef: string | null; lines: CartLine[] }
  | { phase: "done"; attemptRef: string; lines: CartLine[]; classification: Classification };

type Deps = {
  cart: CartStore;
  attempts: AttemptStore;
  fetch?: ClientSetup["fetch"];
  storage?: Pick<Storage, "getItem" | "setItem">;
  locks?: LockManager;
};

class Timeout extends Error {}

export function createCheckoutFlow({ cart, attempts, fetch, storage, locks }: Deps) {
  let session: Session = { phase: "idle" };
  let lastContact: Contact | null = null;
  const listeners = new Set<() => void>();
  const set = (next: Session) => {
    session = next;
    listeners.forEach((listener) => listener());
  };

  async function send(attemptRef: string, lines: CartLine[], contact: Contact): Promise<CheckoutResponse> {
    const client = createGatewayClient(fetch ? { fetch } : undefined);
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        controller.abort();
        reject(new Timeout());
      }, CHECKOUT_TIMEOUT_MS);
    });
    try {
      const { response, data, error } = await Promise.race([
        client.POST("/api/checkout", {
          params: { header: { "idempotency-key": attemptRef } },
          body: { customer_id: customerId(storage), items: lines, contact },
          signal: controller.signal,
        }),
        timeout,
      ]);
      return { status: response.status, body: data ?? error };
    } catch {
      // Network failure, timeout or an unreadable body: the server may still have the order.
      return { networkError: true };
    } finally {
      clearTimeout(timer);
    }
  }

  return {
    getSession: () => session,
    /** The contact details of the last attempt in this tab, in memory only (for Try again). */
    lastContact: () => lastContact,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    /** Back to the form (after a definite outcome, or to leave a finished screen). */
    reset() {
      if (session.phase !== "submitting") set({ phase: "idle" });
    },
    async place(lines: CartLine[], contact: Contact): Promise<PlaceResult> {
      // Synchronous guard first: a second press before any await sends nothing.
      if (session.phase === "submitting") return { kind: "busy" };
      const snapshot = lines.map(({ product_id, quantity }) => ({ product_id, quantity }));
      set({ phase: "submitting", attemptRef: null, lines: snapshot });
      let begun: Awaited<ReturnType<typeof beginAttempt>>;
      try {
        begun = await beginAttempt(snapshot, { storage, locks });
      } catch {
        // Storage or locks refused outright: nothing was recorded, so nothing is sent.
        begun = { ok: false, blockedBy: null, reason: "storage" };
      }
      attempts.refresh();
      if (!begun.ok) {
        set({ phase: "idle" });
        return begun.reason ? { kind: "blocked", blockedBy: null, reason: begun.reason } : { kind: "blocked", blockedBy: begun.blockedBy };
      }
      const { attemptRef } = begun;
      lastContact = contact;
      set({ phase: "submitting", attemptRef, lines: snapshot });

      const classification = classifyCheckout(await send(attemptRef, snapshot, contact), attemptRef);
      if (classification.kind === "definite") {
        if (classification.result.outcome === "processing") attempts.mark(attemptRef, "processing");
        else attempts.settle(attemptRef);
        if (classification.result.outcome === "confirmed") {
          cart.subtract(snapshot);
          lastContact = null;
        }
      } else {
        attempts.mark(attemptRef, "unknown");
      }
      set({ phase: "done", attemptRef, lines: snapshot, classification });
      return classification;
    },
  };
}

export type CheckoutFlow = ReturnType<typeof createCheckoutFlow>;
