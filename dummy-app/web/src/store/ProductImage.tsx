import { productImage } from "./catalog.ts";
import styles from "./ProductImage.module.css";

/** Every product image in the artboards (collection, cart, checkout) is a 4:5 portrait tile. */
const FRAME = { width: 400, height: 500 } as const;

type ProductImageProps = {
  productId: string;
  /** Empty (decorative) by default: the product name is always shown beside the image. */
  alt?: string;
  /** Above the fold (the first collection tile): load eagerly. */
  priority?: boolean;
  className?: string;
};

export function ProductImage({ productId, alt = "", priority = false, className }: ProductImageProps) {
  const image = productImage(productId);
  return (
    <div
      className={[styles.frame, className].filter(Boolean).join(" ")}
      style={{ aspectRatio: "4 / 5" }}
      data-placeholder={image.placeholder || undefined}
    >
      {image.src ? (
        <img
          className={styles.image}
          src={image.src}
          alt={alt}
          width={FRAME.width}
          height={FRAME.height}
          loading={priority ? "eager" : "lazy"}
          decoding="async"
        />
      ) : null}
    </div>
  );
}
