import { useId, useState } from "react";
import { Icon } from "../../shared/ui/Icon.tsx";
import { countLabel } from "../../shared/ui/CountBadge.tsx";
import { displayName } from "../catalog.ts";
import { formatMoney } from "../money.ts";
import { ProductImage } from "../ProductImage.tsx";
import styles from "./OrderSummary.module.css";

export type SummaryLine = {
  product_id: string;
  /** The API's name, for products the display map does not know. */
  apiName?: string | null;
  quantity: number;
  unitCents: number | null;
  totalCents: number | null;
  /** Shown under the line in the fault colour ("No longer available"). */
  problem?: string | null;
};

export type SummaryData = { lines: SummaryLine[]; units: number; subtotalCents: number | null; totalCents: number | null };

const ITEM = ["item", "items"] as const;
const money = (cents: number | null) => (cents == null ? "—" : formatMoney(cents));

function SummaryBody({ data }: { data: SummaryData }) {
  return (
    <>
      <ul className={styles.lines}>
        {data.lines.map((line) => {
          const name = displayName({ product_id: line.product_id, name: line.apiName ?? line.product_id });
          return (
            <li key={line.product_id} className={styles.line} data-problem={line.problem ? "" : undefined}>
              <ProductImage productId={line.product_id} className={styles.thumb} />
              <div className={styles.lineText}>
                <p className={styles.name}>{name}</p>
                <p className={styles.each}>
                  Qty {line.quantity}
                  {line.unitCents != null ? ` · ${formatMoney(line.unitCents)} each` : ""}
                </p>
                {line.problem ? (
                  <p className={styles.problem}>
                    <span className={styles.ring} aria-hidden="true" />
                    {line.problem}
                  </p>
                ) : null}
              </div>
              <p className={styles.lineTotal}>{money(line.totalCents)}</p>
            </li>
          );
        })}
      </ul>
      <dl className={styles.totals}>
        <div className={styles.row}>
          <dt>Subtotal · {countLabel(data.units, ITEM)}</dt>
          <dd>{money(data.subtotalCents)}</dd>
        </div>
        <div className={`${styles.row} ${styles.muted}`}>
          <dt>Shipping</dt>
          <dd>Not applicable (demo)</dd>
        </div>
        <div className={`${styles.row} ${styles.total}`}>
          <dt>Total</dt>
          <dd>{money(data.totalCents)}</dd>
        </div>
      </dl>
    </>
  );
}

/**
 * The order summary (Checkout and Outcome artboards): a card beside the page on wide screens,
 * a "Show order summary" disclosure on phones (Checkout 375, Mobile outcomes). Every figure is
 * the server's: the cart quote on the form, the checkout result on an outcome.
 */
export function OrderSummary({ data, placement }: { data: SummaryData; placement: "card" | "disclosure" }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  if (placement === "card") {
    return (
      <section className={styles.card} aria-labelledby={id}>
        <h2 id={id} className={styles.title}>
          Order summary
        </h2>
        <SummaryBody data={data} />
      </section>
    );
  }
  return (
    <details className={styles.disclosure} onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className={styles.summary}>
        <span>{open ? "Hide order summary" : "Show order summary"}</span>
        <span className={styles.summaryTotal}>
          {money(data.totalCents)}
          <Icon name="chevron-down" className={styles.chevron} />
        </span>
      </summary>
      <section aria-labelledby={id} className={styles.disclosureBody}>
        <h2 id={id} className="visually-hidden">
          Order summary
        </h2>
        <SummaryBody data={data} />
      </section>
    </details>
  );
}
