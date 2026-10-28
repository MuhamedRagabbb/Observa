import type { ReactNode } from "react";
import styles from "./Shell.module.css";
import { Wordmark } from "./ui/Brand.tsx";

type ShellProps = {
  /** Accessible name of the page's main region. */
  regionLabel: string;
  /** Optional surface label shown after the wordmark, such as "Operator". */
  surface?: string;
  /** Header links, aligned to the end of the header. */
  nav?: ReactNode;
  /** A status shown before the links (the operator's controller connection). Not navigation. */
  status?: ReactNode;
  children?: ReactNode;
};

export function Shell({ regionLabel, surface, nav, status, children }: ShellProps) {
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <Wordmark className={styles.wordmark} />
        {surface ? <span className={styles.surface}>{surface}</span> : null}
        {status ? <div className={styles.status}>{status}</div> : null}
        {nav ? <nav className={styles.nav}>{nav}</nav> : null}
      </header>
      <main className={styles.main} aria-label={regionLabel}>
        {children}
      </main>
    </div>
  );
}
