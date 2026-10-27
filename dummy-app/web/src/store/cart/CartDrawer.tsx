import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { BrandMark } from "../../shared/ui/Brand.tsx";
import { Button, ButtonLink } from "../../shared/ui/Button.tsx";
import { countLabel } from "../../shared/ui/CountBadge.tsx";
import { Icon } from "../../shared/ui/Icon.tsx";
import { Notice } from "../../shared/ui/Notice.tsx";
import { QuantityStepper } from "../../shared/ui/QuantityStepper.tsx";
import { TextButton } from "../../shared/ui/TextLink.tsx";
import { displayName } from "../catalog.ts";
import { COLLECTION_ID } from "../home/anchors.ts";
import { formatMoney } from "../money.ts";
import { ProductImage } from "../ProductImage.tsx";
import { MAX_QUANTITY, type CartLine } from "./cart-store.ts";
import { useUnconfirmedAttempts } from "../checkout/attempts.ts";
import { useCart } from "./CartContext.tsx";
import styles from "./CartDrawer.module.css";
import { useCartQuote, type CartQuote, type QuoteLine } from "./quote.ts";

const ITEM = ["item", "items"] as const;
const TITLE_ID = "cart-title";
const FOCUSABLE = "button:not([disabled]), a[href], [tabindex]:not([tabindex='-1'])";

const REASONS: Record<NonNullable<QuoteLine["unavailable_reason"]>, string> = {
  out_of_stock: "Out of stock",
  insufficient_stock: "Not enough in stock",
  unknown_product: "No longer available",
};

/** "1 item is out of stock." when every problem is stock; otherwise the general wording. */
function unavailableMessage(unavailable: QuoteLine[]) {
  const allOut = unavailable.every((line) => line.unavailable_reason === "out_of_stock");
  const count = unavailable.length;
  return `${countLabel(count, ITEM)} ${count === 1 ? "is" : "are"} ${allOut ? "out of stock" : "unavailable"}.`;
}

/**
 * Makes everything outside the cart inert and stops the page scrolling while it is open, so
 * pointer, keyboard and screen-reader users stay inside it (aria-modal alone is not enough).
 */
function useModalPage(host: HTMLElement) {
  useLayoutEffect(() => {
    const others = [...document.body.children].filter((el) => el !== host && !el.hasAttribute("inert"));
    for (const el of others) el.setAttribute("inert", "");
    const { overflow, paddingRight } = document.body.style;
    // The scrollbar disappears with overflow hidden; padding by its width keeps the page from
    // shifting sideways behind the cart. The fixed cart layer still spans the whole viewport.
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    document.body.style.overflow = "hidden";
    if (scrollbar > 0) document.body.style.paddingRight = `${scrollbar}px`;
    return () => {
      for (const el of others) el.removeAttribute("inert");
      document.body.style.overflow = overflow;
      document.body.style.paddingRight = paddingRight;
    };
  }, [host]);
}

/**
 * The cart: a drawer from the right on wide screens (Cart drawer artboards), a full-height
 * sheet on phones (Cart sheet 375). A modal dialog: focus moves to its title, Tab stays inside,
 * Escape and the close button return focus to the opener. Prices and availability come only
 * from the quote. Proceed to Checkout is a link only when the quote is current, every line is
 * available and no earlier attempt is unconfirmed; otherwise it is disabled and a hint says why.
 */
export function CartDrawer() {
  const { store, lines, close } = useCart();
  const quote = useCartQuote(lines);
  const pending = useUnconfirmedAttempts()[0];
  const host = useRef<HTMLElement | null>(null);
  host.current ??= document.createElement("div");
  const panel = useRef<HTMLDivElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useLayoutEffect(() => {
    const node = host.current as HTMLElement;
    document.body.append(node);
    return () => node.remove();
  }, []);
  useModalPage(host.current);
  useEffect(() => heading.current?.focus(), []);
  // The status text arrives after the region exists, so screen readers announce it.
  const [announce, setAnnounce] = useState(false);
  useEffect(() => setAnnounce(true), []);

  const remove = (productIds: string[]) => {
    store.remove(productIds);
    // The pressed button is gone; the title is a stable place to continue from.
    heading.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab" || !panel.current) return;
    const focusable = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (el) => el.getAttribute("aria-hidden") !== "true",
    );
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first || !last) {
      event.preventDefault();
      return;
    }
    const active = document.activeElement;
    if (event.shiftKey && (active === first || active === heading.current)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const data = quote.data;
  const view = lines.length === 0 ? "empty" : data ? "ready" : quote.isError ? "error" : "loading";
  // The quote on screen belongs to the previous lines while a new one loads.
  const stale = quote.isPlaceholderData;
  const summary =
    view === "ready" && data && !stale
      ? `${countLabel(data.unit_count, ITEM)}${data.unavailable_count > 0 ? ` · ${data.unavailable_count} unavailable` : ""}`
      : null;

  // When the view that held focus goes away (Try again succeeded), continue from the title
  // instead of the page body, where Escape and the Tab trap would no longer apply.
  const previousView = useRef(view);
  useEffect(() => {
    const before = previousView.current;
    previousView.current = view;
    if (before === view || (before !== "error" && before !== "loading")) return;
    const active = document.activeElement;
    if (!active || active === document.body || !panel.current?.contains(active)) heading.current?.focus();
  }, [view]);

  return createPortal(
    <div className={styles.layer}>
      <div className={styles.backdrop} aria-hidden="true" onClick={() => close()} />
      <div
        ref={panel}
        className={styles.panel}
        // Focusable, so a click on a blank part keeps focus (and Escape) inside the cart.
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby={TITLE_ID}
        aria-busy={view === "loading" || quote.isFetching || undefined}
        onKeyDown={onKeyDown}
      >
        <div className={styles.head}>
          <h2 id={TITLE_ID} ref={heading} className={styles.title} tabIndex={-1}>
            Cart
          </h2>
          {summary ? <p className={styles.summary}>{summary}</p> : null}
          <button type="button" className={styles.close} aria-label="Close cart" onClick={() => close()}>
            <Icon name="close" />
          </button>
        </div>
        <p className="visually-hidden" role="status">
          {announce && view === "loading" ? "Loading your cart…" : ""}
        </p>

        {view === "empty" ? (
          <div className={styles.message}>
            <BrandMark className={styles.mark} />
            <p className={styles.messageTitle}>Your cart is empty.</p>
            <p className={styles.messageBody}>Objects you add will appear here.</p>
            <ButtonLink
              to={`/#${COLLECTION_ID}`}
              icon="arrow-right"
              variant="secondary"
              onClick={() => close({ restoreFocus: false })}
            >
              Shop the collection
            </ButtonLink>
          </div>
        ) : view === "error" ? (
          <div className={styles.message} role="alert">
            <BrandMark className={styles.mark} />
            <p className={styles.messageTitle}>Your cart couldn’t load.</p>
            <p className={styles.messageBody}>Please try again.</p>
            <Button icon="refresh" variant="secondary" loading={quote.isFetching} onClick={() => void quote.refetch()}>
              Try again
            </Button>
          </div>
        ) : view === "loading" ? (
          <LoadingCart count={lines.length} />
        ) : (
          <ReadyCart
            lines={lines}
            quote={data as CartQuote}
            stale={stale || quote.isFetching}
            pendingRef={pending?.attempt_ref ?? null}
            onRemove={remove}
            onQuantity={store.setQuantity}
            onProceed={() => close({ restoreFocus: false })}
          />
        )}
      </div>
    </div>,
    host.current,
  );
}

function ReadyCart({
  lines,
  quote,
  stale,
  pendingRef,
  onRemove,
  onQuantity,
  onProceed,
}: {
  lines: CartLine[];
  quote: CartQuote;
  /** The quote is for the previous lines: money is shown as updating, never out of date. */
  stale: boolean;
  /** An earlier checkout attempt whose outcome is unknown: new checkouts wait for it. */
  pendingRef: string | null;
  onRemove: (productIds: string[]) => void;
  onQuantity: (productId: string, quantity: number) => void;
  onProceed: () => void;
}) {
  const byId = new Map(quote.lines.map((line) => [line.product_id, line]));
  // Only lines still in the cart count: right after a removal the previous quote is still shown.
  const inCart = new Set(lines.map((line) => line.product_id));
  const unavailableLines = quote.lines.filter((line) => !line.available && inCart.has(line.product_id));
  const unavailable = unavailableLines.map((line) => line.product_id);
  return (
    <>
      <div className={styles.body}>
        {unavailable.length > 0 ? (
          <div className={styles.notice}>
            <Notice
              icon="ring"
              action={
                <TextButton aria-label="Remove unavailable items" onClick={() => onRemove(unavailable)}>
                  Remove
                </TextButton>
              }
            >
              {unavailableMessage(unavailableLines)}
            </Notice>
          </div>
        ) : null}
        <ul className={styles.lines}>
          {lines.map((line) => {
            const priced = byId.get(line.product_id);
            const name = displayName({ product_id: line.product_id, name: priced?.name ?? "Item" });
            const available = priced?.available ?? true;
            // Too few in stock can be fixed by lowering the quantity; out of stock cannot.
            const fixable = available || priced?.unavailable_reason === "insufficient_stock";
            const unit = priced?.unit_price_cents;
            return (
              <li key={line.product_id} className={styles.line} data-unavailable={!available || undefined}>
                <ProductImage productId={line.product_id} className={styles.thumb} />
                <div className={styles.lineInfo}>
                  <h3 className={styles.lineName}>{name}</h3>
                  <p className={styles.lineTotal}>
                    {!available
                      ? "Unavailable"
                      : stale || priced?.line_total_cents == null
                        ? "Updating…"
                        : formatMoney(priced.line_total_cents)}
                  </p>
                  <p className={styles.lineUnit}>
                    {!available && priced?.unavailable_reason
                      ? REASONS[priced.unavailable_reason]
                      : unit == null
                        ? "Updating…"
                        : line.quantity > 1
                          ? `${line.quantity} × ${formatMoney(unit)} each`
                          : `${formatMoney(unit)} each`}
                  </p>
                  <div className={styles.lineControls}>
                    <QuantityStepper
                      itemName={name}
                      value={line.quantity}
                      min={1}
                      max={MAX_QUANTITY}
                      onChange={(quantity) => onQuantity(line.product_id, quantity)}
                      disabled={!fixable}
                    />
                    <TextButton aria-label={`Remove ${name}`} onClick={() => onRemove([line.product_id])}>
                      Remove
                    </TextButton>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
      <div className={styles.foot}>
        <p className={styles.subtotal}>
          <span>
            Subtotal {stale ? null : <span className={styles.muted}>· {countLabel(quote.unit_count, ITEM)}</span>}
          </span>
          <span className={stale ? `${styles.amount} ${styles.updating}` : styles.amount}>
            {stale ? "Updating…" : formatMoney(quote.subtotal_cents)}
          </span>
        </p>
        <p className={styles.note}>Demo checkout. No real payment or shipping.</p>
        {unavailable.length > 0 ? (
          <p className={styles.hint}>
            <span className={styles.ring} aria-hidden="true" />
            Remove unavailable items to continue.
          </p>
        ) : null}
        {pendingRef ? (
          <p className={styles.hint}>
            <span className={styles.ring} aria-hidden="true" />
            Your last order ({pendingRef}) isn’t confirmed yet. New checkouts are paused until its status is known.
          </p>
        ) : null}
        <div className={styles.proceed}>
          {!stale && quote.can_checkout && unavailable.length === 0 && !pendingRef ? (
            <ButtonLink to="/checkout" icon="arrow-right" size="large" block onClick={onProceed}>
              Proceed to Checkout
            </ButtonLink>
          ) : (
            <Button icon="arrow-right" size="large" block disabled>
              Proceed to Checkout
            </Button>
          )}
        </div>
      </div>
    </>
  );
}

/** The loading artboard: grey frames where lines and the footer will be. */
function LoadingCart({ count }: { count: number }) {
  return (
    <>
      <div className={styles.body} aria-hidden="true">
        <ul className={styles.lines}>
          {Array.from({ length: Math.min(count, 4) }, (_, index) => (
            <li key={index} className={styles.line}>
              <div className={`${styles.thumb} ${styles.skeletonTile}`} />
              <div className={styles.lineInfo}>
                <div className={styles.skeleton} style={{ width: "70%", height: "1.25rem" }} />
                <div className={styles.skeleton} style={{ width: "40%", height: "0.75rem" }} />
                <div className={styles.skeleton} style={{ width: "8rem", height: "2.75rem" }} />
              </div>
            </li>
          ))}
        </ul>
      </div>
      <div className={styles.foot} aria-hidden="true">
        <div className={styles.skeletonRow}>
          <div className={styles.skeleton} style={{ width: "8rem", height: "1rem" }} />
          <div className={styles.skeleton} style={{ width: "6rem", height: "1.5rem" }} />
        </div>
        <div className={styles.skeleton} style={{ width: "65%", height: "0.75rem" }} />
        <div className={styles.skeleton} style={{ width: "100%", height: "var(--control-height-large)" }} />
      </div>
    </>
  );
}
