import type { ReactNode } from "react";
import { StatusMarker } from "../../shared/ui/StatusMarker.tsx";
import { displayName } from "../catalog.ts";
import { formatMoney } from "../money.ts";
import { ProductImage } from "../ProductImage.tsx";
import type { Product } from "../products.ts";
import styles from "./ProductCard.module.css";

type ProductCardProps = {
  product: Product;
  /** The large first tile of the collection (Journal / Notebook in the artboards). */
  feature?: boolean;
  /** The card's action, such as "Add to cart". Empty until the cart works (P4-C). */
  action?: ReactNode;
};

/** One product: image, artboard name, API price and availability from API stock. */
export function ProductCard({ product, feature = false, action }: ProductCardProps) {
  const inStock = product.stock > 0;
  return (
    <article className={styles.card} data-feature={feature || undefined}>
      <ProductImage productId={product.product_id} priority={feature} />
      <div className={styles.info}>
        <h3 className={styles.name}>{displayName(product)}</h3>
        <p className={styles.price}>{formatMoney(product.price_cents)}</p>
        <div className={styles.status}>
          {inStock ? (
            <StatusMarker shape="filled" tone="success">
              In stock
            </StatusMarker>
          ) : (
            <StatusMarker shape="hollow" tone="muted">
              Out of stock
            </StatusMarker>
          )}
        </div>
        {action ? <div className={styles.action}>{action}</div> : null}
      </div>
    </article>
  );
}
