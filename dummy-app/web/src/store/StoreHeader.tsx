import type { Ref } from "react";
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
      /** Phones hide the main navigation: one link that stays reachable there (My Orders). */
      phoneLink?: { to: string; label: string };
      /** The Search button: it controls the field named by `controls` while that is open. */
      search?: { onOpen: () => void; expanded?: boolean; controls?: string; buttonRef?: Ref<HTMLButtonElement> };
      /** Present only once the cart works (P4-C). */
      cart?: { count: number; onOpen: () => void };
      back?: never;
    }
  | {
      /** Focused pages (checkout, outcomes): the wordmark and one way back, no navigation. */
      back: { to: string; label: string; state?: unknown };
      links?: never;
      phoneLink?: never;
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
        <Link to={props.back.to} state={props.back.state} className={styles.back}>
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
            {props.phoneLink ? (
              // Before Experiments: on phones it sits to its left, so the tab order follows the screen.
              <NavLink to={props.phoneLink.to} className={`${styles.utility} ${styles.phoneLink}`}>
                {props.phoneLink.label}
              </NavLink>
            ) : null}
            <CrossAppLink target="control" className={styles.experiments}>
              Experiments
            </CrossAppLink>
            {props.search || props.cart ? <span className={styles.divider} aria-hidden="true" /> : null}
            {props.search ? (
              <button
                ref={props.search.buttonRef}
                type="button"
                className={`${styles.utility} ${styles.search}`}
                aria-expanded={props.search.expanded}
                aria-controls={props.search.expanded ? props.search.controls : undefined}
                onClick={props.search.onOpen}
              >
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
