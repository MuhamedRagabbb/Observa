import { Outlet } from "react-router";
import { CartDrawer } from "./cart/CartDrawer.tsx";
import { CartProvider, useCart } from "./cart/CartContext.tsx";
import { COLLECTION_ID } from "./home/anchors.ts";
import { StoreHeader, type HeaderLink } from "./StoreHeader.tsx";
import styles from "./StoreLayout.module.css";

/**
 * What the header offers today, besides the Cart. My Orders (P4-F) and Search (P4-G) join it in
 * the slices that make them work.
 */
const LINKS: HeaderLink[] = [
  { to: "/", label: "Home", end: true },
  { to: `/#${COLLECTION_ID}`, label: "Products", section: true },
];

export function StoreLayout() {
  return (
    <CartProvider>
      <Page />
    </CartProvider>
  );
}

function Page() {
  const cart = useCart();
  return (
    <div className={styles.page}>
      <StoreHeader links={LINKS} cart={{ count: cart.units, onOpen: cart.open }} />
      <main className={styles.main} aria-label="Storefront">
        <Outlet />
      </main>
      {cart.isOpen ? <CartDrawer /> : null}
    </div>
  );
}
