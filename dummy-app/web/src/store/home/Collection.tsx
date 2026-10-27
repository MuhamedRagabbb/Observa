import { useEffect, useRef } from "react";
import { Button } from "../../shared/ui/Button.tsx";
import { countLabel } from "../../shared/ui/CountBadge.tsx";
import { useProducts } from "../products.ts";
import { COLLECTION_ID } from "./anchors.ts";
import styles from "./Collection.module.css";
import { ProductCard } from "./ProductCard.tsx";

const OBJECT = ["object", "objects"] as const;
const TITLE_ID = `${COLLECTION_ID}-title`;

/** "The Collection": the real products, in API order, laid out as the artboards' asymmetric grid. */
export function Collection() {
  const products = useProducts();
  const heading = useRef<HTMLHeadingElement>(null);
  const retried = useRef(false);
  const shown = products.data ?? [];
  // Products already on screen stay there if a background refresh fails.
  const view = shown.length > 0 ? "list" : products.isPending ? "loading" : products.isError ? "error" : "empty";

  // After "Try again" succeeds its button is gone; focus moves to the heading, not the page body.
  useEffect(() => {
    if (retried.current && view !== "error" && !products.isFetching) {
      retried.current = false;
      heading.current?.focus();
    }
  }, [view, products.isFetching]);

  return (
    <section
      id={COLLECTION_ID}
      className={styles.collection}
      aria-labelledby={TITLE_ID}
      aria-busy={view === "loading" || undefined}
    >
      <div className={styles.head}>
        {/* Focusable so "Shop the collection", the Products link and Try again can move focus here. */}
        <h2 id={TITLE_ID} ref={heading} className={styles.title} tabIndex={-1}>
          The Collection
        </h2>
        {view === "list" ? <p className={styles.count}>{countLabel(shown.length, OBJECT)}</p> : null}
      </div>
      {/* Always present, so screen readers announce its text when loading starts. */}
      <p className="visually-hidden" role="status">
        {view === "loading" ? "Loading the collection…" : ""}
      </p>

      {view === "loading" ? (
        <ul className={styles.grid} aria-hidden="true">
          {Array.from({ length: 5 }, (_, index) => (
            <li key={index} className={styles.item}>
              <div className={styles.skeletonImage} />
              <div className={styles.skeletonLine} />
              <div className={`${styles.skeletonLine} ${styles.skeletonShort}`} />
            </li>
          ))}
        </ul>
      ) : view === "error" ? (
        <div className={styles.message} role="alert">
          <p className={styles.messageTitle}>The collection didn’t load.</p>
          <p className={styles.messageBody}>Check your connection, then try again.</p>
          <Button
            icon="refresh"
            loading={products.isFetching}
            onClick={() => {
              retried.current = true;
              void products.refetch();
            }}
          >
            Try again
          </Button>
        </div>
      ) : view === "empty" ? (
        <div className={styles.message}>
          <p className={styles.messageTitle}>No objects are available right now.</p>
          <p className={styles.messageBody}>New pieces are added from time to time.</p>
        </div>
      ) : (
        <ul className={styles.grid}>
          {shown.map((product, index) => (
            <li key={product.product_id} className={styles.item}>
              <ProductCard product={product} feature={index === 0} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
