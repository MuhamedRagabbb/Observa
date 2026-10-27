import { useEffect } from "react";
import { Outlet, useLocation, useMatch, useNavigate } from "react-router";
import { CartDrawer } from "./cart/CartDrawer.tsx";
import { CartProvider, useCart } from "./cart/CartContext.tsx";
import { OPEN_CART } from "./checkout/CheckoutPage.tsx";
import { startCheckoutRecovery } from "./checkout/session.ts";
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
  useEffect(() => startCheckoutRecovery(), []);
  const checkout = useMatch("/checkout") !== null;
  const location = useLocation();
  const navigate = useNavigate();
  const openCart = (location.state as { openCart?: boolean } | null)?.openCart === true;

  // "Return to cart" and "Review cart" arrive with { openCart: true }: open it once, then drop
  // the flag so Back and reload do not reopen it.
  useEffect(() => {
    if (!openCart) return;
    cart.open();
    navigate(`${location.pathname}${location.hash}`, { replace: true, state: null });
  }, [openCart, cart, navigate, location.pathname, location.hash]);

  return (
    <div className={styles.page} data-focused={checkout || undefined}>
      {checkout ? (
        <StoreHeader back={{ to: "/", label: "Return to cart", state: OPEN_CART }} />
      ) : (
        <StoreHeader links={LINKS} cart={{ count: cart.units, onOpen: cart.open }} />
      )}
      <main className={styles.main} aria-label="Storefront">
        <Outlet />
      </main>
      {cart.isOpen ? <CartDrawer /> : null}
    </div>
  );
}
