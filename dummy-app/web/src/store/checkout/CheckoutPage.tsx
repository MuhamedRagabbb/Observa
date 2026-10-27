import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router";
import { Button, ButtonLink } from "../../shared/ui/Button.tsx";
import { Icon } from "../../shared/ui/Icon.tsx";
import { Notice } from "../../shared/ui/Notice.tsx";
import { TextButton, TextLink } from "../../shared/ui/TextLink.tsx";
import { useCart } from "../cart/CartContext.tsx";
import type { CartLine } from "../cart/cart-store.ts";
import { useCartQuote, type CartQuote } from "../cart/quote.ts";
import { displayName } from "../catalog.ts";
import { formatMoney } from "../money.ts";
import { useAttemptsSynced, useUnconfirmedAttempts, type AttemptRecord } from "./attempts.ts";
import styles from "./CheckoutPage.module.css";
import { validateContact, type Contact, type ContactErrors } from "./contact.ts";
import type { Notice as SessionNotice } from "./flow.ts";
import type { CheckoutResult } from "./outcome.ts";
import { OrderSummary, type SummaryData } from "./OrderSummary.tsx";
import { checkoutFlow, useCheckoutSession } from "./session.ts";

/** "Return to cart" and "Review cart": back to the store with the cart open. */
export const OPEN_CART = { openCart: true } as const;

const sameLines = (quote: CartQuote, lines: readonly CartLine[]) => {
  if (quote.lines.length !== lines.length) return false;
  const quoted = new Map(quote.lines.map((line) => [line.product_id, line.quantity]));
  return lines.every((line) => quoted.get(line.product_id) === line.quantity);
};

function quoteSummary(quote: CartQuote | undefined, lines: readonly CartLine[]): SummaryData {
  const byId = new Map(quote?.lines.map((line) => [line.product_id, line]));
  return {
    lines: lines.map((line) => {
      const priced = byId.get(line.product_id);
      return {
        product_id: line.product_id,
        apiName: priced?.name,
        quantity: line.quantity,
        unitCents: priced?.unit_price_cents ?? null,
        totalCents: priced?.available ? (priced.line_total_cents ?? null) : null,
        problem: priced && !priced.available ? "No longer available" : null,
      };
    }),
    units: quote?.unit_count ?? 0,
    subtotalCents: quote?.subtotal_cents ?? null,
    totalCents: quote?.subtotal_cents ?? null,
  };
}

function resultSummary(result: CheckoutResult): SummaryData {
  const unavailable = new Set((result.unavailable_items ?? []).map((item) => String(item.product_id)));
  return {
    lines: result.items.map((item) => ({
      product_id: item.product_id,
      quantity: item.quantity,
      unitCents: item.unit_price_cents ?? null,
      totalCents: item.unit_price_cents == null ? null : item.unit_price_cents * item.quantity,
      problem: unavailable.has(item.product_id) ? "No longer available" : null,
    })),
    // The attempt as sent, so the units agree with the result's total (Outcome 3b artboard).
    units: result.items.reduce((sum, item) => sum + item.quantity, 0),
    subtotalCents: result.total_cents,
    totalCents: result.total_cents,
  };
}

/** The page title takes focus when a screen appears, so screen readers start from it. */
function useFocusOnMount<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    // A new screen starts at the top (Place Order sits low on phones), with its title focused.
    window.scrollTo({ top: 0 });
    ref.current?.focus({ preventScroll: true });
  }, []);
  return ref;
}

function Layout({ main, summary, summaryFirstOnPhones }: { main: ReactNode; summary: SummaryData; summaryFirstOnPhones?: boolean }) {
  return (
    <div className={styles.layout} data-summary-first={summaryFirstOnPhones ? "" : undefined}>
      <div className={styles.main}>{main}</div>
      {/* Phones: below an outcome (Mobile outcome artboards), above the form (Checkout 375). */}
      <div className={styles.phoneSummary}>
        <OrderSummary data={summary} placement="disclosure" />
      </div>
      <aside className={styles.side}>
        <OrderSummary data={summary} placement="card" />
      </aside>
    </div>
  );
}

/** R4: after a confirmed order the address carries its reference, so a reload can re-read it. */
const ATTEMPT_PARAM = "attempt";

/**
 * /checkout. Which screen shows, in order of precedence:
 * 1. this tab's attempt: placing, then its outcome (or its pending state while it is checked);
 * 2. any unconfirmed attempt in this browser (a reload, another tab): that attempt, with Check
 *    status, and no form, because its outcome could be a placed order;
 * 3. R4: a confirmed order named in the address, re-read read-only;
 * 4. an empty cart: nothing to submit;
 * 5. the form.
 */
export function CheckoutPage() {
  const { lines } = useCart();
  const session = useCheckoutSession();
  const unconfirmed = useUnconfirmedAttempts();
  const synced = useAttemptsSynced();
  const flow = checkoutFlow();
  const [params, setParams] = useSearchParams();
  const wanted = params.get(ATTEMPT_PARAM);
  const [restoring, setRestoring] = useState<string | null>(null);

  // Leaving the page forgets a finished screen (Back after success shows no stale outcome);
  // an answer still on its way keeps the session and settles it when it arrives.
  useEffect(() => () => flow.reset(), [flow]);

  // R4: only once the gate has been read, only if this browser holds no unconfirmed attempt
  // (that one is shown and checked instead), and only by a read-only lookup.
  const mayRestore = wanted !== null && synced && unconfirmed.length === 0 && session.phase === "idle";
  useEffect(() => {
    if (!mayRestore || !wanted) return;
    let live = true;
    setRestoring(wanted);
    void flow.restore(wanted).then((found) => {
      if (!live) return;
      setRestoring(null);
      if (!found) setParams({}, { replace: true });
    });
    return () => {
      live = false;
      setRestoring(null);
    };
  }, [mayRestore, wanted, flow, setParams]);

  if (session.phase === "done") {
    const { classification, attemptRef } = session;
    if (classification.kind === "definite" && classification.result.outcome !== "processing") {
      return <OutcomeScreen key={attemptRef} result={classification.result} />;
    }
    const held = unconfirmed.find((a) => a.attempt_ref === attemptRef);
    // This tab's own attempt is briefly missing from the list while the gate is re-read.
    const record: AttemptRecord = held ?? { attempt_ref: attemptRef, customer_id: session.customerId, lines: session.lines, created_at: "", status: "unknown" };
    const processing = classification.kind === "definite" || record.status === "processing";
    return <PendingScreen key={attemptRef} record={record} status={processing ? "processing" : record.status} activity={session.activity} notice={session.notice} />;
  }
  if (session.phase !== "submitting") {
    const pending: AttemptRecord | undefined = unconfirmed[0];
    if (pending) return <PendingScreen key={pending.attempt_ref} record={pending} status={pending.status} />;
    if (restoring || (wanted !== null && !synced)) return <RestoringScreen />;
    if (lines.length === 0) return <EmptyCheckout />;
  }
  return <CheckoutForm lines={session.phase === "submitting" ? session.lines : lines} submitting={session.phase === "submitting"} />;
}

function RestoringScreen() {
  return (
    <div className={styles.empty}>
      <h1 className={styles.title}>Checkout</h1>
      <p className={styles.muted} role="status">
        Loading your order…
      </p>
    </div>
  );
}

function Field({
  label,
  value,
  error,
  onChange,
  inputRef,
  type = "text",
  placeholder,
  disabled,
}: {
  label: string;
  value: string;
  error?: string;
  onChange: (value: string) => void;
  inputRef: React.RefObject<HTMLInputElement | null>;
  type?: "text" | "email";
  placeholder: string;
  disabled: boolean;
}) {
  const id = useId();
  return (
    <div className={styles.field}>
      <label htmlFor={id} className={styles.label}>
        {label}
      </label>
      <input
        ref={inputRef}
        id={id}
        className={styles.input}
        type={type}
        value={value}
        placeholder={placeholder}
        // Demo only: browsers should not offer the visitor's real details.
        autoComplete="off"
        spellCheck={false}
        readOnly={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {error ? (
        <p id={`${id}-error`} className={styles.error}>
          {error}
        </p>
      ) : null}
    </div>
  );
}

function CheckoutForm({ lines, submitting }: { lines: CartLine[]; submitting: boolean }) {
  const flow = checkoutFlow();
  const quote = useCartQuote(lines);
  const [contact, setContact] = useState<Contact>(() => flow.lastContact() ?? { name: "", email: "" });
  const [errors, setErrors] = useState<ContactErrors>({});
  const [checked, setChecked] = useState(false);
  const [refused, setRefused] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const emailRef = useRef<HTMLInputElement>(null);
  const heading = useFocusOnMount<HTMLHeadingElement>();

  const data = quote.data;
  // Only a quote for exactly these lines may back Place Order: never an older one still on screen.
  // An errored refresh keeps the old data in the cache: never trust it.
  const current = data !== undefined && !quote.isPlaceholderData && !quote.isFetching && !quote.isError && sameLines(data, lines);
  const ready = current && data.can_checkout;
  const update = (key: keyof Contact) => (value: string) => {
    const next = { ...contact, [key]: value };
    setContact(next);
    if (checked) setErrors(validateContact(next));
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (submitting || !ready) return;
    const found = validateContact(contact);
    setErrors(found);
    setChecked(true);
    if (found.name) return nameRef.current?.focus();
    if (found.email) return emailRef.current?.focus();
    setRefused(false);
    void flow.place(lines, contact).then((placed) => {
      if (placed.kind === "blocked" && placed.reason === "storage") setRefused(true);
    });
  };

  const total = current ? formatMoney(data.subtotal_cents) : null;
  return (
    <Layout
      summaryFirstOnPhones
      summary={quoteSummary(current ? data : undefined, lines)}
      main={
        <form className={styles.form} onSubmit={submit} noValidate aria-busy={submitting || undefined}>
          <h1 ref={heading} tabIndex={-1} className={styles.title}>
            Checkout
          </h1>
          <section className={styles.section} aria-labelledby="contact-title">
            <h2 id="contact-title" className={styles.sectionTitle}>
              Contact
            </h2>
            <p className={styles.hint}>For this demo only. Please don’t enter real personal details.</p>
            <Field label="Full name" value={contact.name} error={errors.name} onChange={update("name")} inputRef={nameRef} placeholder="Demo name" disabled={submitting} />
            <Field label="Email" type="email" value={contact.email} error={errors.email} onChange={update("email")} inputRef={emailRef} placeholder="demo@example.com" disabled={submitting} />
          </section>
          <section className={styles.section} aria-labelledby="payment-title">
            <h2 id="payment-title" className={styles.sectionTitle}>
              Payment
            </h2>
            {/* The only method: shown as chosen, not offered as a choice. */}
            <div className={styles.method}>
              <span className={styles.radio} aria-hidden="true" />
              <span className={styles.methodName}>Simulated payment</span>
              <span className={styles.methodNote}>Demo only</span>
            </div>
            <Notice icon="info">This is a demo store. No card is charged and nothing is shipped.</Notice>
          </section>
          <div className={styles.submit}>
            <Button type="submit" icon="arrow-right" size="large" block loading={submitting} unavailable={!submitting && !ready}>
              {submitting ? "Placing order…" : total ? `Place Order · ${total}` : "Place Order"}
            </Button>
            <div className={styles.submitNote} role="status">
              {submitting ? (
                <>
                  <span className={styles.spinner} aria-hidden="true" />
                  <span>
                    <strong className={styles.strong}>Submitting your order…</strong>
                    <span className={styles.muted}>Please keep this page open. We’ll show the result as soon as it’s confirmed.</span>
                  </span>
                </>
              ) : refused ? (
                <span className={styles.error}>
                  Your browser didn’t let this page save the order’s reference, so nothing was sent. Allow site data for this store, then try again.
                </span>
              ) : quote.isError ? (
                <span className={styles.muted}>
                  Prices couldn’t load.{" "}
                  <TextButton onClick={() => void quote.refetch()}>Try again</TextButton>
                </span>
              ) : current && !data.can_checkout ? (
                <span className={styles.muted}>Some items are no longer available. Review your cart to continue.</span>
              ) : !current ? (
                <span className={styles.muted}>Updating prices…</span>
              ) : null}
            </div>
          </div>
        </form>
      }
    />
  );
}

const FAULT_ICON = <Icon name="x-circle" className={styles.faultIcon} />;

function Eyebrow({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <p className={styles.eyebrow}>
      {icon}
      {children}
    </p>
  );
}

function OutcomeScreen({ result }: { result: CheckoutResult }) {
  const flow = checkoutFlow();
  const heading = useFocusOnMount<HTMLHeadingElement>();
  const [, setParams] = useSearchParams();
  const confirmedRef = result.outcome === "confirmed" ? result.attempt_ref : null;
  // R4: the reference only (never contact details), replacing this history entry.
  useEffect(() => {
    if (confirmedRef) setParams({ [ATTEMPT_PARAM]: confirmedRef }, { replace: true });
  }, [confirmedRef, setParams]);
  // Back to the form, details kept: a fresh quote of the cart as it is now backs the next
  // attempt, which gets a new reference.
  const tryAgain = () => flow.reset();
  const retryButton = (
    <Button icon="refresh" size="large" onClick={tryAgain}>
      Try again
    </Button>
  );
  const returnLink = (
    <TextLink to="/" state={OPEN_CART}>
      Return to cart
    </TextLink>
  );

  let content: { eyebrow: ReactNode; title: string; body: ReactNode; actions: ReactNode };
  if (result.outcome === "confirmed") {
    content = {
      eyebrow: (
        <Eyebrow icon={<span className={styles.confirmIcon} aria-hidden="true"><Icon name="check" /></span>}>Order confirmed</Eyebrow>
      ),
      title: "Thank you.",
      body: (
        <>
          <p className={styles.lede}>Your demo order has been placed. No payment was taken and nothing will be shipped.</p>
          <Reference label="Order ID" spoken="order ID" value={result.order_number ?? ""} copy />
        </>
      ),
      actions: (
        <>
          <ButtonLink to="/" icon="arrow-right" size="large">
            Continue shopping
          </ButtonLink>
          <TextLink to="/orders">View My Orders</TextLink>
        </>
      ),
    };
  } else if (result.outcome === "payment_failed") {
    const declined = result.error_code === "payment_declined";
    content = {
      eyebrow: <Eyebrow icon={FAULT_ICON}>{declined ? "Payment declined" : "Payment unavailable"}</Eyebrow>,
      title: declined ? "Payment didn’t go through." : "Payment couldn’t be completed.",
      body: (
        <p className={styles.lede}>
          {declined
            ? "The payment service confirmed the simulated payment was declined, so your order wasn’t placed and nothing was charged. It’s safe to try again."
            : "The payment service was unavailable, so your order wasn’t placed and nothing was charged. It’s safe to try again."}
        </p>
      ),
      actions: (
        <>
          {retryButton}
          {returnLink}
        </>
      ),
    };
  } else if (result.outcome === "unavailable") {
    const names = (result.unavailable_items ?? []).map((item) => displayName({ product_id: String(item.product_id), name: String(item.product_id) }));
    content = {
      eyebrow: <Eyebrow icon={FAULT_ICON}>Order not placed</Eyebrow>,
      title: "We couldn’t place your order.",
      body: (
        <p className={styles.lede}>
          {names.length > 0 ? (
            <>
              <strong className={styles.strong}>{names.join(", ")}</strong> {names.length === 1 ? "is" : "are"} no longer available, so your order wasn’t placed and nothing was charged. Remove {names.length === 1 ? "it" : "them"} from your cart to continue.
            </>
          ) : (
            "Some items are no longer available, so your order wasn’t placed and nothing was charged. Review your cart to continue."
          )}
        </p>
      ),
      actions: (
        <ButtonLink to="/" state={OPEN_CART} icon="arrow-right" size="large">
          Review cart
        </ButtonLink>
      ),
    };
  } else if (result.outcome === "abandoned") {
    content = {
      eyebrow: <Eyebrow icon={FAULT_ICON}>Order not placed</Eyebrow>,
      title: "Your order wasn’t placed.",
      body: <p className={styles.lede}>This attempt was stopped before anything was placed or charged. It’s safe to try again.</p>,
      actions: (
        <>
          {retryButton}
          {returnLink}
        </>
      ),
    };
  } else {
    content = {
      eyebrow: <Eyebrow icon={FAULT_ICON}>Order not placed</Eyebrow>,
      title: "Something went wrong.",
      body: (
        <p className={styles.lede}>
          {result.retryable
            ? "Your order wasn’t placed and nothing was charged. It’s safe to try again."
            : "Your order wasn’t placed and nothing was charged. Return to your cart to continue."}
        </p>
      ),
      actions: result.retryable ? (
        <>
          {retryButton}
          {returnLink}
        </>
      ) : (
        <ButtonLink to="/" state={OPEN_CART} icon="arrow-left" size="large">
          Return to cart
        </ButtonLink>
      ),
    };
  }

  return (
    <Layout
      summary={resultSummary(result)}
      main={
        <div className={styles.outcome}>
          {content.eyebrow}
          <h1 ref={heading} tabIndex={-1} className={styles.outcomeTitle}>
            {content.title}
          </h1>
          {content.body}
          <div className={styles.actions}>{content.actions}</div>
        </div>
      }
    />
  );
}

/**
 * Outcome 4 (and the 202 Processing variant): the attempt may or may not have been placed. New
 * checkouts stay paused until its status is known. Check status asks the server (GET first;
 * resolve only after a 404, or when polling gave up on a processing attempt). A processing
 * attempt is polled automatically, with GET only, for about 60 s.
 */
const PENDING: Record<AttemptRecord["status"] | "still_processing", { eyebrow: string; title: string; body: string }> = {
  unknown: {
    eyebrow: "Status unknown",
    title: "We couldn’t confirm your order.",
    body: "We didn’t get a clear answer, so your order may or may not have been placed. To avoid a duplicate order, new checkouts are paused until its status is known.",
  },
  // A record still marked as sending: after a reload, or still in flight in another tab.
  submitting: {
    eyebrow: "Status unknown",
    title: "We’re still waiting to confirm your order.",
    body: "We haven’t had an answer for this order yet, so it may or may not have been placed. To avoid a duplicate order, new checkouts are paused until its status is known.",
  },
  processing: {
    eyebrow: "Processing",
    title: "Your order is still processing.",
    body: "The order service is still working on it. To avoid a duplicate order, new checkouts are paused until it finishes.",
  },
  still_processing: {
    eyebrow: "Processing",
    title: "Your order is still processing.",
    body: "This is taking longer than usual. It may still go through, so new checkouts stay paused. Check again in a moment.",
  },
};

function PendingScreen({
  record,
  status,
  activity,
  notice,
}: {
  record: AttemptRecord;
  status: AttemptRecord["status"];
  activity?: "checking" | "polling";
  notice?: SessionNotice;
}) {
  const flow = checkoutFlow();
  const quote = useCartQuote(record.lines);
  const heading = useFocusOnMount<HTMLHeadingElement>();
  const gaveUp = status === "processing" && notice === "still_processing";
  const polls = status === "processing" && !gaveUp && activity !== "checking";
  const latest = useRef(record);
  latest.current = record;

  // Processing: poll with GET only while this screen is shown; leaving it stops polling.
  useEffect(() => {
    if (!polls) return;
    const following = flow.follow(latest.current);
    return following.stop;
  }, [polls, flow, record.attempt_ref]);

  const copy = PENDING[gaveUp ? "still_processing" : status];
  const checking = activity === "checking";
  const check = () => {
    if (!checking) void flow.check(latest.current);
  };
  const statusLine = checking
    ? "Checking your order’s status…"
    : notice === "check_failed"
      ? "We couldn’t check its status just now. Your order is still paused; please try again in a moment."
      : polls
        ? "Checking for an update automatically…"
        : "";
  return (
    <Layout
      summary={quoteSummary(quote.data && !quote.isPlaceholderData ? quote.data : undefined, record.lines)}
      main={
        <div className={styles.outcome}>
          <Eyebrow icon={<Icon name="clock" className={styles.clockIcon} />}>{copy.eyebrow}</Eyebrow>
          <h1 ref={heading} tabIndex={-1} className={styles.outcomeTitle}>
            {copy.title}
          </h1>
          <p className={styles.lede}>{copy.body}</p>
          <Reference label="Attempt reference" value={record.attempt_ref} />
          <p className={styles.checkStatus} role="status">
            {statusLine}
          </p>
          <div className={styles.actions}>
            {polls ? null : (
              // Outcome 4 artboard: "Check Order Status" with the arrow, then Return to cart.
              <Button icon={gaveUp ? "refresh" : "arrow-right"} size="large" loading={checking} unavailable={checking} onClick={check}>
                {checking ? "Checking…" : gaveUp ? "Check again" : "Check Order Status"}
              </Button>
            )}
            <TextLink to="/" state={OPEN_CART}>
              Return to cart
            </TextLink>
          </div>
        </div>
      }
    />
  );
}

function Reference({ label, spoken = label.toLowerCase(), value, copy = false }: { label: string; spoken?: string; value: string; copy?: boolean }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");
  const doCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  };
  return (
    <div className={styles.reference}>
      <div>
        <p className={styles.referenceLabel}>
          {label}
        </p>
        <p className={styles.referenceValue}>{value}</p>
      </div>
      {copy ? (
        <div className={styles.copy}>
          <TextButton icon="copy" aria-label={`Copy ${spoken}`} onClick={() => void doCopy()}>
            Copy
          </TextButton>
          <span className="visually-hidden" role="status">
            {copied === "copied" ? `${label} copied` : copied === "failed" ? `Couldn’t copy. Select the ${spoken} to copy it.` : ""}
          </span>
        </div>
      ) : null}
    </div>
  );
}

function EmptyCheckout() {
  const heading = useFocusOnMount<HTMLHeadingElement>();
  return (
    <div className={styles.empty}>
      <h1 ref={heading} tabIndex={-1} className={styles.title}>
        Checkout
      </h1>
      <p className={styles.emptyTitle}>Your cart is empty.</p>
      <p className={styles.muted}>Add something from the collection to check out.</p>
      <ButtonLink to="/#collection" icon="arrow-right" variant="secondary">
        Shop the collection
      </ButtonLink>
    </div>
  );
}
