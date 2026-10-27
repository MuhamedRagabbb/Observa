import { useEffect, useRef } from "react";
import { useParams } from "react-router";
import { Button } from "../../shared/ui/Button.tsx";
import { TextLink } from "../../shared/ui/TextLink.tsx";
import { useCartQuote } from "../cart/quote.ts";
import { displayName } from "../catalog.ts";
import { checkoutFlow } from "../checkout/session.ts";
import { formatMoney } from "../money.ts";
import { ProductImage } from "../ProductImage.tsx";
import type { OrderRow, OrderStatus } from "./merge.ts";
import { useAttemptLookup, useOrderRows } from "./orders-api.ts";
import styles from "./Orders.module.css";
import { CheckStatusButton, formatDate, notPlaced, OrderStatusMarker, unitsLabel } from "./presentation.tsx";

const ORDER_NUMBER = /^OBS-[0-9]+-[0-9A-F]{4}$/;
const ATTEMPT_REF = /^ATT-[0-9A-F]{4}-[0-9]{4}$/;

/** What the note under the title says, by status. */
const NOTES: Record<OrderStatus, string> = {
  confirmed: "Demo order. No payment was taken and nothing will be shipped.",
  processing: "The order service is still working on this order. New checkouts are paused until it finishes.",
  unknown: "We couldn’t confirm whether this order was placed. Check its status before ordering again to avoid a duplicate.",
  declined: "The simulated payment was declined, so this order wasn’t placed and nothing was charged.",
  payment_failed: "The payment service was unavailable, so this order wasn’t placed and nothing was charged.",
  not_placed: "This order wasn’t placed and nothing was charged.",
  failed: "Something went wrong, so this order wasn’t placed and nothing was charged.",
};

/**
 * /orders/:ref, by order number (OBS-…) or attempt reference (ATT-…). Found in the merged list;
 * an attempt reference that is neither listed nor held here (older than the 50 newest) is looked
 * up read-only. Nothing on this page resolves except Check status on an attempt held here.
 */
export function OrderDetailPage() {
  const { ref = "" } = useParams();
  const valid = ORDER_NUMBER.test(ref) || ATTEMPT_REF.test(ref);
  const { rows, query, synced, customer } = useOrderRows();
  useEffect(() => () => checkoutFlow().reset(), []);
  const row = valid ? rows.find((r) => r.reference === ref || r.attemptRef === ref) : undefined;
  // A row that was shown and then left the list (its attempt settled here or in another tab) is
  // waited for until the list has been reread, rather than looked up separately.
  const shown = useRef<{ ref: string; at: number } | null>(null);
  // A failed reread also ends the wait (it shows Try again below).
  const answeredAt = Math.max(query.dataUpdatedAt, query.errorUpdatedAt);
  if (row) shown.current = { ref, at: answeredAt };
  const awaitingReread = !row && shown.current?.ref === ref && shown.current.at === answeredAt;
  const listed = !query.isPending && !query.isFetching && synced && !awaitingReread;
  const lookup = useAttemptLookup(valid && listed && !row && ATTEMPT_REF.test(ref) ? ref : null, customer);

  if (!valid) return <NotFound />;
  if (row) return <Detail row={row} onRefresh={() => void query.refetch()} refreshing={query.isFetching} />;
  if (!listed || lookup.isLoading) return <Loading />;
  if (lookup.data) return <Detail row={lookup.data} onRefresh={() => void lookup.refetch()} refreshing={lookup.isFetching} />;
  if (query.isError || lookup.isError) {
    return (
      <Frame>
        <h1 className={styles.detailTitle} tabIndex={-1}>
          We couldn’t load this order.
        </h1>
        <p className={styles.muted}>Please try again.</p>
        <Button
          variant="secondary"
          icon="refresh"
          onClick={() => {
            void query.refetch();
            if (lookup.isError) void lookup.refetch();
          }}
        >
          Try again
        </Button>
      </Frame>
    );
  }
  return <NotFound />;
}

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <div className={styles.detail}>
      <TextLink to="/orders" icon="arrow-left">
        All orders
      </TextLink>
      <div className={styles.detailBody}>{children}</div>
    </div>
  );
}

function Loading() {
  return (
    <Frame>
      <p className={styles.muted} role="status">
        Loading your order…
      </p>
    </Frame>
  );
}

function NotFound() {
  return (
    <Frame>
      <h1 className={styles.detailTitle}>We couldn’t find this order.</h1>
      <p className={styles.muted}>It may belong to another browser, or the link may be incomplete.</p>
    </Frame>
  );
}

function Detail({ row, onRefresh, refreshing }: { row: OrderRow; onRefresh: () => void; refreshing: boolean }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus({ preventScroll: true }), [row.status]);
  // Lines the server has not priced (an unconfirmed attempt): today's prices, for reference.
  const unpriced = row.items.some((item) => item.unit_price_cents === null);
  const quote = useCartQuote(unpriced ? row.items.map(({ product_id, quantity }) => ({ product_id, quantity })) : []);
  const priced = new Map(quote.data && !quote.isPlaceholderData ? quote.data.lines.map((line) => [line.product_id, line]) : []);
  const unitPrice = (item: OrderRow["items"][number]) => item.unit_price_cents ?? priced.get(item.product_id)?.unit_price_cents ?? null;
  const total = row.totalCents ?? (unpriced && quote.data && !quote.isPlaceholderData ? quote.data.subtotal_cents : null);
  const date = formatDate(row.createdAt);
  const meta = [date, unitsLabel(row.units)].filter(Boolean).join(" · ");

  return (
    <Frame>
      <OrderStatusMarker status={row.status} />
      <h1 ref={heading} tabIndex={-1} className={styles.detailTitle}>
        {row.kind === "order" ? "Order" : "Order attempt"}
      </h1>
      <p className={styles.detailRef}>{row.reference}</p>
      <p className={styles.muted}>{meta}</p>
      <div className={styles.note}>
        <p>{NOTES[row.status]}</p>
        {row.status === "unknown" ? <p className={styles.noteRef}>{row.attemptRef}</p> : null}
      </div>
      {row.status === "unknown" && row.record ? (
        <CheckStatusButton record={row.record} size="large" />
      ) : row.status === "processing" ? (
        row.record ? (
          <CheckStatusButton record={row.record} size="large" />
        ) : (
          <Button icon="refresh" size="large" loading={refreshing} onClick={onRefresh}>
            Check status
          </Button>
        )
      ) : null}
      {row.items.length > 0 ? (
        <ul className={styles.lines}>
          {row.items.map((item) => {
            const price = unitPrice(item);
            const name = displayName({ product_id: item.product_id, name: item.product_id });
            return (
              <li key={item.product_id} className={styles.line}>
                <ProductImage productId={item.product_id} className={styles.lineImage} />
                <div className={styles.lineInfo}>
                  <p className={styles.lineName}>{name}</p>
                  <p className={styles.muted}>{price === null ? `Qty ${item.quantity}` : `Qty ${item.quantity} · ${formatMoney(price)} each`}</p>
                </div>
                <p className={styles.lineTotal}>{price === null ? "—" : formatMoney(price * item.quantity)}</p>
              </li>
            );
          })}
        </ul>
      ) : null}
      <div className={styles.detailTotal} data-testid="order-total">
        <span>Total</span>
        <span className={styles.detailTotalValue}>
          {total === null ? "—" : formatMoney(total)}
          {notPlaced(row.status) ? <span className={styles.totalNote}>Not charged</span> : row.status === "unknown" ? <span className={styles.totalNote}>Unconfirmed</span> : null}
        </span>
      </div>
    </Frame>
  );
}
