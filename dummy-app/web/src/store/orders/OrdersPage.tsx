import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { BrandMark } from "../../shared/ui/Brand.tsx";
import { Button, ButtonLink } from "../../shared/ui/Button.tsx";
import { Icon } from "../../shared/ui/Icon.tsx";
import { TextButton } from "../../shared/ui/TextLink.tsx";
import { useCartQuote } from "../cart/quote.ts";
import { closedStore } from "../checkout/closed.ts";
import { checkoutFlow } from "../checkout/session.ts";
import { formatMoney } from "../money.ts";
import { ProductImage } from "../ProductImage.tsx";
import type { OrderRow } from "./merge.ts";
import { useOrderRows } from "./orders-api.ts";
import styles from "./Orders.module.css";
import {
  CART_UNCHANGED,
  CheckStatusButton,
  formatDate,
  notPlaced,
  OrderStatusMarker,
  statusLabel,
  unitsLabel,
} from "./presentation.tsx";

type Announce = (message: string) => void;
/** Rows whose Check status is under way: when one comes back settled, it takes focus. */
type Answered = { current: Set<string> };

/** Order details live at /orders/<order number>, or the attempt reference when there is none. */
export const orderPath = (row: Pick<OrderRow, "reference">) =>
  `/orders/${row.reference}`;

/**
 * /orders. The customer's orders (at most the 50 newest, from the server) merged with this
 * browser's unconfirmed attempts. Unconfirmed attempts are always listed, even when the list
 * cannot load, so they can still be checked.
 */
export function OrdersPage() {
  const { rows, query, limited } = useOrderRows();
  const [announcement, setAnnouncement] = useState("");
  // Kept here, not in the row: a settled attempt's row leaves the list until it is reread.
  const answered = useRef(new Set<string>());
  // One polite region for the page: a row's Check status result is said here.
  const announcer = (
    <p className="visually-hidden" role="status" data-testid="orders-announcer">
      {announcement}
    </p>
  );
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus({ preventScroll: true }), []);
  // A check made here leaves no outcome screen waiting on /checkout.
  useEffect(() => () => checkoutFlow().reset(), []);

  const title = (
    <h1 ref={heading} tabIndex={-1} className={styles.title}>
      My Orders
    </h1>
  );

  if (query.isPending) {
    return (
      <div className={styles.page}>
        <div className={styles.head}>{title}</div>
        <LoadingRows />
      </div>
    );
  }

  // Every attempt this browser holds, whatever an earlier list said about it.
  const held = rows.filter((row) => row.record !== null);
  // Attempts Check status found never placed: kept as a reminder, not listed by the server.
  const closed = rows.filter((row) => row.note !== null);
  if (query.isError && query.data === undefined) {
    return (
      <div className={styles.page}>
        <div className={styles.head}>{title}</div>
        <div className={styles.state}>
          <h2 className={styles.stateTitle}>We couldn’t load your orders.</h2>
          <p className={styles.muted}>Please try again.</p>
          <Button
            variant="secondary"
            icon="refresh"
            loading={query.isFetching}
            onClick={() => void query.refetch()}
          >
            Try again
          </Button>
        </div>
        {held.length > 0 ? (
          <section className={styles.heldSection} aria-labelledby="held-title">
            <h2 id="held-title" className={styles.sectionTitle}>
              Unconfirmed in this browser
            </h2>
            <OrderTable rows={held} announce={setAnnouncement} answered={answered} />
          </section>
        ) : null}
        {closed.length > 0 ? (
          <section className={styles.heldSection} aria-labelledby="closed-title">
            <h2 id="closed-title" className={styles.sectionTitle}>
              Not placed in this browser
            </h2>
            <OrderTable rows={closed} announce={setAnnouncement} answered={answered} />
          </section>
        ) : null}
        {announcer}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className={styles.page}>
        <div className={styles.head}>{title}</div>
        <div className={styles.state}>
          <BrandMark className={styles.emptyMark} />
          <h2 className={styles.stateTitle}>No orders yet.</h2>
          <p className={styles.muted}>Orders you place will appear here.</p>
          <ButtonLink to="/#collection" icon="arrow-right" variant="secondary">
            Shop the collection
          </ButtonLink>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.page}>
      <div className={styles.head}>
        {title}
        <p
          className={styles.count}
        >{`${rows.length} ${rows.length === 1 ? "order" : "orders"}`}</p>
      </div>
      {query.isError ? (
        <div className={styles.refreshFailed} role="status">
          <p>We couldn’t refresh your orders.</p>
          <Button variant="secondary" icon="refresh" loading={query.isFetching} onClick={() => void query.refetch()}>
            Try again
          </Button>
        </div>
      ) : null}
      {limited ? (
        <p className={styles.limit}>Showing your 50 most recent orders</p>
      ) : null}
      <OrderTable rows={rows} announce={setAnnouncement} answered={answered} />
      {announcer}
    </div>
  );
}

function LoadingRows() {
  return (
    <div
      className={styles.loading}
      data-testid="orders-loading"
      aria-busy="true"
    >
      <p className="visually-hidden" role="status">
        Loading your orders…
      </p>
      {[0, 1, 2].map((row) => (
        <div key={row} className={styles.loadingRow} aria-hidden="true">
          {[0, 1, 2, 3, 4, 5].map((cell) => (
            <span key={cell} className={styles.bar} data-cell={cell} />
          ))}
        </div>
      ))}
    </div>
  );
}

/**
 * A table on wide screens, cards on phones (the same markup restyled; explicit roles keep the
 * table semantics when rows are laid out as blocks).
 */
function OrderTable({ rows, announce, answered }: { rows: OrderRow[]; announce: Announce; answered: Answered }) {
  return (
    <table className={styles.table} role="table" aria-label="Your orders">
      <thead className={styles.thead} role="rowgroup">
        <tr role="row">
          <th role="columnheader" scope="col">
            Order
          </th>
          <th role="columnheader" scope="col">
            Date
          </th>
          <th role="columnheader" scope="col">
            Items
          </th>
          <th role="columnheader" scope="col">
            Total
          </th>
          <th role="columnheader" scope="col">
            Status
          </th>
          <th role="columnheader" scope="col">
            <span className="visually-hidden">Action</span>
          </th>
        </tr>
      </thead>
      <tbody role="rowgroup">
        {rows.map((row) => (
          <Row key={row.attemptRef} row={row} announce={announce} answered={answered} />
        ))}
      </tbody>
    </table>
  );
}

function Row({ row, announce, answered }: { row: OrderRow; announce: Announce; answered: Answered }) {
  const checkable = row.record !== null && row.status === "unknown";
  // A definite Check status answer replaces the button: focus moves to the row's View link
  // (the row stays, keyed by its attempt reference) and the new status is announced.
  // A never-placed attempt has no order to view: its Try again link takes focus instead.
  const next = useRef<HTMLAnchorElement>(null);
  const reminder = row.note !== null;
  useEffect(() => {
    if (!answered.current.has(row.attemptRef) || row.status === "unknown") return;
    answered.current.delete(row.attemptRef);
    next.current?.focus();
    announce(`${row.attemptRef}: ${statusLabel(row.status)}${reminder ? `. ${CART_UNCHANGED}` : ""}`);
  }, [row.status, row.attemptRef, reminder, announce, answered]);
  const muted = row.kind === "attempt";
  const date = formatDate(row.createdAt);
  return (
    <tr className={styles.row} role="row" data-status={row.status}>
      <td role="cell" className={styles.refCell}>
        <div className={styles.refStack}>
          {checkable || reminder ? (
            <Link
              to={orderPath(row)}
              className={styles.reference}
              data-muted=""
            >
              {row.reference}
            </Link>
          ) : (
            <span className={styles.reference} data-muted={muted || undefined}>
              {row.reference}
            </span>
          )}
          {row.status === "unknown" ? (
            <span className={styles.tag}>Attempt reference</span>
          ) : notPlaced(row.status) ? (
            <span className={styles.tag}>Attempt · not placed</span>
          ) : null}
        </div>
      </td>
      <td role="cell" className={styles.dateCell}>
        {date ?? "—"}
        <span className={styles.phoneUnits}> · {unitsLabel(row.units)}</span>
      </td>
      <td role="cell" className={styles.itemsCell}>
        <span className={styles.thumbs} aria-hidden="true">
          {row.items.slice(0, 3).map((item) => (
            <ProductImage
              key={item.product_id}
              productId={item.product_id}
              className={styles.thumb}
            />
          ))}
        </span>
        <span>{unitsLabel(row.units)}</span>
      </td>
      <td role="cell" className={styles.totalCell}>
        <RowTotal row={row} />
      </td>
      <td role="cell" className={styles.statusCell}>
        <OrderStatusMarker status={row.status} />
        {reminder ? <span className={styles.cartNote}>{CART_UNCHANGED}</span> : null}
      </td>
      <td role="cell" className={styles.actionCell}>
        {checkable && row.record ? (
          <CheckStatusButton
            record={row.record}
            onStart={() => answered.current.add(row.attemptRef)}
            onAnswer={(definite) => {
              if (!definite) answered.current.delete(row.attemptRef);
            }}
          />
        ) : reminder ? (
          <span className={styles.closedActions}>
            <ButtonLink ref={next} to="/checkout" icon="refresh" variant="secondary" aria-label={`Try again: ${row.attemptRef}`}>
              Try again
            </ButtonLink>
            <TextButton aria-label={`Dismiss ${row.attemptRef}`} onClick={() => closedStore().dismiss(row.attemptRef)}>
              Dismiss
            </TextButton>
          </span>
        ) : (
          <Link
            ref={next}
            to={orderPath(row)}
            className={styles.view}
            aria-label={`View order ${row.reference}`}
          >
            View <Icon name="arrow-right" className={styles.viewIcon} />
          </Link>
        )}
      </td>
    </tr>
  );
}

function RowTotal({ row }: { row: OrderRow }) {
  // An attempt the server has not priced: today's prices for its lines, marked unconfirmed.
  const quote = useCartQuote(
    row.totalCents === null && row.status === "unknown"
      ? row.items.map(({ product_id, quantity }) => ({ product_id, quantity }))
      : [],
  );
  const cents =
    row.totalCents ??
    (row.status === "unknown" && quote.data && !quote.isPlaceholderData
      ? quote.data.subtotal_cents
      : null);
  return (
    <>
      <span
        className={styles.total}
        data-muted={row.kind === "attempt" || undefined}
      >
        {cents === null ? "—" : formatMoney(cents)}
      </span>
      {row.status === "unknown" ? (
        <span className={styles.totalNote}>Unconfirmed</span>
      ) : notPlaced(row.status) ? (
        <span className={styles.totalNote}>Not charged</span>
      ) : null}
    </>
  );
}
