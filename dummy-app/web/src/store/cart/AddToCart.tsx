import { Button } from "../../shared/ui/Button.tsx";
import { displayName } from "../catalog.ts";
import type { Product } from "../products.ts";
import { MAX_LINES, MAX_QUANTITY } from "./cart-store.ts";
import { useCart } from "./CartContext.tsx";

/**
 * The collection card's action (Product Collection artboards): "Add to cart", or a disabled
 * "Sold out" when the API reports no stock. At the backend's limits (10 of a product, 10
 * products) it becomes unavailable but keeps focus, and its name says why. Each name starts
 * with the visible words (WCAG 2.5.3). The header's Cart count announces each add.
 */
export function AddToCart({ product }: { product: Product }) {
  const { store, lines } = useCart();
  const name = displayName(product);
  const inCart = lines.find((line) => line.product_id === product.product_id)?.quantity ?? 0;

  if (product.stock <= 0) {
    return (
      <Button icon="minus" disabled aria-label={`Sold out: ${name}`}>
        Sold out
      </Button>
    );
  }
  if (inCart >= MAX_QUANTITY) {
    return (
      <Button icon="plus" unavailable aria-label={`${MAX_QUANTITY} in cart: ${name}, the most per order`}>
        {MAX_QUANTITY} in cart
      </Button>
    );
  }
  if (inCart === 0 && lines.length >= MAX_LINES) {
    return (
      <Button icon="plus" unavailable aria-label={`Cart is full: ${MAX_LINES} products, ${name} not added`}>
        Cart is full
      </Button>
    );
  }
  return (
    <Button icon="plus" aria-label={`Add to cart: ${name}`} onClick={() => store.add(product.product_id)}>
      Add to cart
    </Button>
  );
}
