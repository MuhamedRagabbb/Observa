// Placing an order and recovering an unclear one, safely (approved P4 plan, outcome rules):
// 1. place: the gate refuses while any attempt is unconfirmed; otherwise it allocates a
//    reference and stores the attempt BEFORE anything is sent. Without a working gate nothing
//    is sent (fail closed). Exactly one POST /api/checkout, never retried; a 30 s limit turns a
//    hung request into "unknown";
// 2. check (a press of Check status / Check again): GET /api/attempts/{ref} first. Only a 404
//    attempt_not_found, or a still-processing answer after polling gave up, leads to one
//    POST .../resolve, which fences the reference on the server (abandoned = never placed) or
//    settles it. A failed or unclear GET never resolves;
// 3. follow: a processing attempt is polled with GET only, backing off 1, 2, 4, 8, 8 ... s for
//    about 60 s, then reported as still processing;
// 4. a definite outcome settles the attempt in the gate. A confirmed order is removed and its
//    cart effect journalled in one gate transaction; the cart then applies it exactly once
//    (cart-store applyConfirmed), and replayEffects re-applies it after a crash;
// 5. restore (R4): a confirmed order is re-read by reference, read-only.
// Requests carry the reference and the customer ID the attempt was sent with; contact details
// go in the checkout body only, and the in-memory copy serves "Try again".
import { createGatewayClient } from "../../api/gateway-client.ts";
import type { ClientSetup } from "../../api/same-origin-client.ts";
import type { CartLine, CartStore } from "../cart/cart-store.ts";
import { beginAttempt, customerId, type AttemptRecord, type AttemptStore } from "./attempts.ts";
import type { ClosedStore } from "./closed.ts";
import type { Contact } from "./contact.ts";
import { classifyCheckout, isCheckoutResult, type Classification, type CheckoutResponse } from "./outcome.ts";
import { classifyStatus } from "./status.ts";

export const CHECKOUT_TIMEOUT_MS = 30_000;
export const STATUS_TIMEOUT_MS = 15_000;
export const POLL_DELAYS_MS = [1000, 2000, 4000, 8000] as const;
export const POLL_BUDGET_MS = 60_000;
/** A crash-journalled cart effect older than this is not replayed (the cart has moved on). */
export const REPLAY_WINDOW_MS = 24 * 60 * 60 * 1000;
const ATTEMPT_REF = /^ATT-[0-9A-F]{4}-[0-9]{4}$/;

export type PlaceResult = Classification | { kind: "busy" } | { kind: "blocked"; blockedBy: string | null; reason?: "storage" };
export type CheckResult = Classification | { kind: "busy" };
export type FollowEnd = "definite" | "still_processing" | "unknown" | "stopped";

/** Why the screen says more than its outcome: the check failed, or polling gave up. */
export type Notice = "check_failed" | "still_processing";

export type Session =
  | { phase: "idle" }
  | { phase: "submitting"; attemptRef: string | null; lines: CartLine[] }
  | {
      phase: "done";
      attemptRef: string;
      /** The customer ID the attempt was sent with: Check status must ask with the same one. */
      customerId: string;
      lines: CartLine[];
      classification: Classification;
      activity?: "checking" | "polling";
      notice?: Notice;
    };

type Sleep = (ms: number, signal: AbortSignal) => Promise<void>;

type Deps = {
  cart: CartStore;
  attempts: AttemptStore;
  /** Where My Orders keeps its "Not placed" reminder for a fenced attempt (display only). */
  closed?: ClosedStore;
  fetch?: ClientSetup["fetch"];
  storage?: Pick<Storage, "getItem" | "setItem">;
  sleep?: Sleep;
  now?: () => string;
};

const realSleep: Sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException("stopped", "AbortError"));
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("stopped", "AbortError"));
    });
  });

class Timeout extends Error {}

/** One request with a time limit; any failure to get a readable answer is a network error. */
async function exchange(
  limitMs: number,
  signal: AbortSignal | undefined,
  run: (signal: AbortSignal) => Promise<{ response: Response; data?: unknown; error?: unknown }>,
): Promise<CheckoutResponse> {
  // Already stopped (a poll whose screen went away): send nothing.
  if (signal?.aborted) return { networkError: true };
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  signal?.addEventListener("abort", onAbort);
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      controller.abort();
      reject(new Timeout());
    }, limitMs);
  });
  try {
    const { response, data, error } = await Promise.race([run(controller.signal), timeout]);
    return { status: response.status, body: data ?? error };
  } catch {
    // Network failure, timeout or an unreadable body: the server may still have the order.
    return { networkError: true };
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onAbort);
  }
}

export function createCheckoutFlow({ cart, attempts, closed, fetch, storage, sleep = realSleep, now = () => new Date().toISOString() }: Deps) {
  let session: Session = { phase: "idle" };
  let lastContact: Contact | null = null;
  const listeners = new Set<() => void>();
  const checking = new Set<string>();
  const reconciling = new Set<string>();
  const following = new Map<string, { stop: () => void; done: Promise<FollowEnd>; signal: AbortSignal }>();
  /** References whose polling gave up: the next Check again may resolve. */
  const exhausted = new Set<string>();
  const client = () => createGatewayClient(fetch ? { fetch } : undefined);

  const set = (next: Session) => {
    session = next;
    listeners.forEach((listener) => listener());
  };
  /** Shows an attempt's state, unless this tab is busy placing a different one. */
  const show = (record: Pick<AttemptRecord, "attempt_ref" | "customer_id" | "lines">, classification: Classification, extra: Partial<Extract<Session, { phase: "done" }>> = {}) => {
    if (session.phase === "submitting") return;
    set({ phase: "done", attemptRef: record.attempt_ref, customerId: record.customer_id, lines: record.lines, classification, ...extra });
  };
  const shown = (attemptRef: string) => session.phase === "done" && session.attemptRef === attemptRef;

  const getStatus = (record: Pick<AttemptRecord, "attempt_ref" | "customer_id">, signal?: AbortSignal) =>
    exchange(STATUS_TIMEOUT_MS, signal, (s) =>
      client().GET("/api/attempts/{attempt_ref}", {
        params: { path: { attempt_ref: record.attempt_ref }, query: { customer_id: record.customer_id } },
        signal: s,
      }),
    );
  const resolveAttempt = (record: AttemptRecord) =>
    exchange(STATUS_TIMEOUT_MS, undefined, (s) =>
      client().POST("/api/attempts/{attempt_ref}/resolve", {
        params: { path: { attempt_ref: record.attempt_ref }, query: { customer_id: record.customer_id } },
        signal: s,
      }),
    );

  /** Records what an answer means for the attempt and the cart. Only definite answers settle. */
  async function apply(record: Pick<AttemptRecord, "attempt_ref" | "status">, classification: Classification) {
    if (classification.kind === "unknown") {
      // Never downgrade: a processing attempt is known to be recorded on the server (checked in
      // the gate's own transaction, so another tab's processing mark is never overwritten).
      if (record.status !== "processing") await attempts.mark(record.attempt_ref, "unknown", "processing");
      return;
    }
    const { outcome } = classification.result;
    if (outcome === "processing") {
      await attempts.mark(record.attempt_ref, "processing");
    } else if (outcome === "confirmed") {
      const { effect } = await attempts.confirm(record.attempt_ref, now());
      if (effect) cart.applyConfirmed(effect.attempt_ref, effect.lines);
      exhausted.delete(record.attempt_ref);
    } else {
      await attempts.settle(record.attempt_ref);
      exhausted.delete(record.attempt_ref);
    }
  }

  const isProcessing = (c: Classification) => c.kind === "definite" && c.result.outcome === "processing";

  function follow(record: AttemptRecord): { stop: () => void; done: Promise<FollowEnd> } {
    const existing = following.get(record.attempt_ref);
    // A stopped follower may not have finished yet (stop, then follow again at once): start anew.
    if (existing && !existing.signal.aborted) return existing;
    const controller = new AbortController();
    // The latest processing answer, for the screen once polling gives up.
    let latest: Classification | null = shown(record.attempt_ref) && session.phase === "done" && isProcessing(session.classification) ? session.classification : null;
    const run = async (): Promise<FollowEnd> => {
      let waited = 0;
      for (let i = 0; waited < POLL_BUDGET_MS; i++) {
        const delay = POLL_DELAYS_MS[Math.min(i, POLL_DELAYS_MS.length - 1)] ?? 8000;
        try {
          await sleep(delay, controller.signal);
        } catch {
          return "stopped";
        }
        waited += delay;
        if (controller.signal.aborted) return "stopped";
        const answer = classifyStatus(await getStatus(record, controller.signal), record.attempt_ref);
        if (controller.signal.aborted) return "stopped";
        if (answer.kind === "definite" && !isProcessing(answer)) {
          await apply({ ...record, status: "processing" }, answer);
          show(record, answer);
          return "definite";
        }
        if (answer.kind === "not_found") {
          // 202 proved the server recorded it: a 404 now is inconsistent. Leave it to a person.
          const unknown: Classification = { kind: "unknown", reason: "no_result" };
          await attempts.mark(record.attempt_ref, "unknown");
          show(record, unknown, { notice: "check_failed" });
          return "unknown";
        }
        // Still processing, or this poll failed: keep waiting (the attempt stays processing).
        if (isProcessing(answer as Classification)) latest = answer as Classification;
      }
      exhausted.add(record.attempt_ref);
      if (session.phase !== "done" || shown(record.attempt_ref)) {
        show(record, latest ?? { kind: "definite", result: processingPlaceholder(record) }, { notice: "still_processing" });
      }
      return "still_processing";
    };
    if (session.phase === "done" && shown(record.attempt_ref)) set({ ...session, activity: "polling", notice: undefined });
    const done = run().finally(() => {
      if (following.get(record.attempt_ref) !== entry) return; // a newer follower took over
      following.delete(record.attempt_ref);
      if (session.phase === "done" && shown(record.attempt_ref) && session.activity === "polling") set({ ...session, activity: undefined });
    });
    const entry = { stop: () => controller.abort(), done, signal: controller.signal };
    following.set(record.attempt_ref, entry);
    return { stop: entry.stop, done };
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
      const customer = customerId(storage);
      let begun: Awaited<ReturnType<typeof beginAttempt>>;
      try {
        begun = await beginAttempt(snapshot, { customerId: customer, now });
      } catch {
        // The gate refused outright: nothing was recorded, so nothing is sent.
        begun = { ok: false, blockedBy: null, reason: "storage" };
      }
      await attempts.sync();
      if (!begun.ok) {
        set({ phase: "idle" });
        return begun.reason ? { kind: "blocked", blockedBy: null, reason: begun.reason } : { kind: "blocked", blockedBy: begun.blockedBy };
      }
      const { attemptRef } = begun;
      lastContact = contact;
      set({ phase: "submitting", attemptRef, lines: snapshot });

      const response = await exchange(CHECKOUT_TIMEOUT_MS, undefined, (signal) =>
        client().POST("/api/checkout", {
          params: { header: { "idempotency-key": attemptRef } },
          body: { customer_id: customer, items: snapshot, contact },
          signal,
        }),
      );
      const classification = classifyCheckout(response, attemptRef);
      await apply({ attempt_ref: attemptRef, status: "submitting" }, classification);
      if (classification.kind === "definite" && classification.result.outcome === "confirmed") lastContact = null;
      set({ phase: "done", attemptRef, customerId: customer, lines: snapshot, classification });
      return classification;
    },

    /** Check status / Check again, for an attempt this tab is not itself still sending. */
    async check(record: AttemptRecord, { quiet = false }: { quiet?: boolean } = {}): Promise<CheckResult> {
      const ref = record.attempt_ref;
      if (checking.has(ref) || (session.phase === "submitting" && session.attemptRef === ref)) return { kind: "busy" };
      checking.add(ref);
      following.get(ref)?.stop();
      const previous = shown(ref) && session.phase === "done" ? session.classification : ({ kind: "unknown", reason: "no_result" } as Classification);
      // Quiet (My Orders): the answer settles the attempt but is never shown on the checkout page.
      if (!quiet) show(record, previous, { activity: "checking" });
      try {
        const status = classifyStatus(await getStatus(record), ref);
        // Explicitly asked for, and only after a clear GET: fence or settle on the server.
        const classification: Classification =
          status.kind === "not_found" || (isProcessing(status as Classification) && exhausted.has(ref))
            ? classifyCheckout(await resolveAttempt(record), ref)
            : (status as Classification);
        // My Orders: a never-placed attempt is not listed by the server and is about to leave the
        // held list, so leave a reminder first (the row then changes in place instead of vanishing).
        if (quiet && classification.kind === "definite" && classification.result.outcome === "abandoned") closed?.add(record);
        await apply(record, classification);
        exhausted.delete(ref);
        if (!quiet) show(record, classification.kind === "unknown" ? previous : classification, classification.kind === "unknown" ? { notice: "check_failed" } : {});
        return classification;
      } finally {
        checking.delete(ref);
      }
    },

    /** Polls a processing attempt (GET only) until it is definite or about 60 s have passed. */
    follow,

    /**
     * My Orders: the server's list already has an answer for an attempt this browser still
     * holds. A valid result for the same reference, listed for the customer ID the attempt was
     * sent with, settles it exactly as a Check status answer would (a confirmed order's cart
     * effect once, through the gate). Sends nothing and never resolves. True if it changed the attempt.
     */
    async reconcile(record: AttemptRecord, entry: unknown, listedFor: string): Promise<boolean> {
      const ref = record.attempt_ref;
      if (record.customer_id !== listedFor || !isCheckoutResult(entry) || entry.attempt_ref !== ref) return false;
      if (checking.has(ref) || reconciling.has(ref) || (session.phase === "submitting" && session.attemptRef === ref)) return false;
      reconciling.add(ref);
      try {
        await attempts.sync();
        const current = attempts.unconfirmed().find((a) => a.attempt_ref === ref);
        if (!current) return false;
        if (entry.outcome === "processing" && current.status === "processing") return false;
        await apply(current, { kind: "definite", result: entry });
        return true;
      } finally {
        reconciling.delete(ref);
      }
    },

    /** R4: shows a confirmed order again by its reference. Read-only: never resolves, never settles. */
    async restore(attemptRef: string): Promise<boolean> {
      if (!ATTEMPT_REF.test(attemptRef)) return false;
      const customer = customerId(storage);
      const answer = classifyStatus(await getStatus({ attempt_ref: attemptRef, customer_id: customer }), attemptRef);
      if (answer.kind !== "definite" || answer.result.outcome !== "confirmed") return false;
      if (session.phase === "submitting") return false;
      set({ phase: "done", attemptRef, customerId: customer, lines: answer.result.items.map(({ product_id, quantity }) => ({ product_id, quantity })), classification: answer });
      return true;
    },

    /** On start: applies any confirmed order's cart effect a crash may have interrupted. */
    async replayEffects() {
      const cutoff = Date.parse(now()) - REPLAY_WINDOW_MS;
      for (const effect of await attempts.effects()) {
        if (Date.parse(effect.confirmed_at) >= cutoff) cart.applyConfirmed(effect.attempt_ref, effect.lines);
      }
    },
  };
}

/** A stand-in processing result for the screen when polling gave up before any answer arrived. */
function processingPlaceholder(record: Pick<AttemptRecord, "attempt_ref" | "lines">) {
  return {
    attempt_ref: record.attempt_ref,
    order_id: null,
    order_number: null,
    state: "unknown",
    outcome: "processing" as const,
    retryable: false,
    items: record.lines.map((line) => ({ ...line, unit_price_cents: null })),
    total_cents: null,
    error_code: null,
    unavailable_items: null,
    created_at: null,
  };
}

export type CheckoutFlow = ReturnType<typeof createCheckoutFlow>;
