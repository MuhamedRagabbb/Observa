import { Outlet } from "react-router";
import { StoreHeader, type HeaderLink } from "./StoreHeader.tsx";
import styles from "./StoreLayout.module.css";

/**
 * What the header offers today. Products (P4-B), My Orders (P4-F), Search (P4-G) and the Cart
 * (P4-C) join it in the slices that make them work.
 */
const LINKS: HeaderLink[] = [{ to: "/", label: "Home", end: true }];

export function StoreLayout() {
  return (
    <div className={styles.page}>
      <StoreHeader links={LINKS} />
      <main className={styles.main} aria-label="Storefront">
        <Outlet />
      </main>
    </div>
  );
}
