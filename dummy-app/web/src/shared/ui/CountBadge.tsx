import styles from "./CountBadge.module.css";

/** "1 item", "2 items". */
export const countLabel = (count: number, noun: readonly [string, string]) =>
  `${count} ${count === 1 ? noun[0] : noun[1]}`;

/**
 * A number in a black circle, hidden from assistive tech. The owner names the count in words
 * (the Cart button's label) and announces changes from a live region outside any button,
 * because a button's content is not read as a live region.
 */
export function CountBadge({ count }: { count: number }) {
  return (
    <span className={styles.badge} aria-hidden="true">
      {count}
    </span>
  );
}
