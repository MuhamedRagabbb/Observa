import { useEffect } from "react";
import { Outlet, useLocation, useMatch, useNavigate } from "react-router";
import { CartDrawer } from "./cart/CartDrawer.tsx";
import { CartProvider, useCart } from "./cart/CartContext.tsx";
import { OPEN_CART } from "./checkout/CheckoutPage.tsx";
import { startCheckoutRecovery } from "./checkout/session.ts";
import { COLLECTION_ID } from "./home/anchors.ts";
import { SearchProvider, SEARCH_ID, useSearch } from "./search/SearchContext.tsx";
import { StoreHeader, type HeaderLink } from "./StoreHeader.tsx";
import styles from "./StoreLayout.module.css";

/**
 * What the header offers besides the Cart and Search.
 */
const LINKS: HeaderLink[] = [
  { to: "/", label: "Home", end: true },
  { to: `/#${COLLECTION_ID}`, label: "Products", section: true },
  { to: "/orders", label: "My Orders" },
];

export function StoreLayout() {
  return (
    <CartProvider>
      <SearchProvider>
        <Page />
      </SearchProvider>
    </CartProvider>
  );
}

function Page() {
  const cart = useCart();
  const search = useSearch();
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
        <StoreHeader
          links={LINKS}
          phoneLink={{ to: "/orders", label: "My Orders" }}
          search={{ onOpen: search.request, expanded: search.open, controls: SEARCH_ID, buttonRef: search.buttonRef }}
          cart={{ count: cart.units, onOpen: cart.open }}
        />
      )}
      <main className={styles.main} aria-label="Storefront">
        <Outlet />
      </main>
      {cart.isOpen ? <CartDrawer /> : null}
    </div>
  );
}
