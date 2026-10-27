import { Link, NavLink } from "react-router";
import { CrossAppLink } from "../shared/CrossAppLink.tsx";
import { Wordmark } from "../shared/ui/Brand.tsx";
import { CountBadge, countLabel } from "../shared/ui/CountBadge.tsx";
import { Icon } from "../shared/ui/Icon.tsx";
import styles from "./StoreHeader.module.css";

const ITEM = ["item", "items"] as const;

export type HeaderLink = {
  to: string;
  label: string;
  end?: boolean;
  /** A link to a section of a page (such as /#collection): never marked as the current page. */
  section?: boolean;
};

type StoreHeaderProps =
  | {
      /** Main navigation, in order. Pass only links whose page exists. */
      links: HeaderLink[];
      /** Present only once search works (P4-G). */
      search?: { onOpen: () => void };
      /** Present only once the cart works (P4-C). */
      cart?: { count: number; onOpen: () => void };
      back?: never;
    }
  | {
      /** Focused pages (checkout, outcomes): the wordmark and one way back, no navigation. */
      back: { to: string; label: string };
      links?: never;
      search?: never;
      cart?: never;
    };

/**
 * The storefront header. It renders only what the caller hands it, so a control appears only
 * when the feature behind it works (no dead controls, audit item 7).
 */
export function StoreHeader(props: StoreHeaderProps) {
  return (
    <header
      className={styles.header}
      data-variant={props.back ? "focused" : "full"}
      data-tools={props.search || props.cart ? "" : undefined}
    >
      <Link to="/" className={styles.brand}>
        <Wordmark />
      </Link>
      {props.back ? (
        <Link to={props.back.to} className={styles.back}>
          <Icon name="arrow-left" className={styles.backIcon} />
          <span className={styles.backLabel}>{props.back.label}</span>
        </Link>
      ) : (
        <>
          <nav className={styles.nav} aria-label="Main">
            <ul className={styles.navList}>
              {props.links.map((link) => (
                <li key={link.to}>
                  {link.section ? (
                    <Link to={link.to} className={styles.navLink}>
                      {link.label}
                    </Link>
                  ) : (
                    <NavLink to={link.to} end={link.end} className={styles.navLink}>
                      {link.label}
                    </NavLink>
                  )}
                </li>
              ))}
            </ul>
          </nav>
          <div className={styles.utilities}>
            <CrossAppLink target="control" className={styles.experiments}>
              Experiments
            </CrossAppLink>
            {props.search || props.cart ? <span className={styles.divider} aria-hidden="true" /> : null}
            {props.search ? (
              <button type="button" className={`${styles.utility} ${styles.search}`} onClick={props.search.onOpen}>
                <Icon name="search" className={styles.searchIcon} />
                <span className={styles.searchLabel}>Search</span>
              </button>
            ) : null}
            {props.cart ? (
              <button
                type="button"
                className={`${styles.utility} ${styles.cart}`}
                aria-label={`Cart, ${countLabel(props.cart.count, ITEM)}`}
                onClick={props.cart.onOpen}
              >
                Cart <CountBadge count={props.cart.count} />
              </button>
            ) : null}
            {props.cart ? (
              // Announces a change in the count (an item added) without moving focus.
              <span className="visually-hidden" role="status" aria-live="polite">
                {`Cart, ${countLabel(props.cart.count, ITEM)}`}
              </span>
            ) : null}
          </div>
        </>
      )}
    </header>
  );
}
