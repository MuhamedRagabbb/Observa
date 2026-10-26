import type { ReactNode } from "react";
import styles from "./Shell.module.css";

type ShellProps = {
  /** Accessible name of the page's main region. */
  regionLabel: string;
  /** Optional surface label shown after the wordmark, such as "Operator". */
  surface?: string;
  children?: ReactNode;
};

export function Shell({ regionLabel, surface, children }: ShellProps) {
  return (
    <div className={styles.page}>
      <header className={styles.header}>
        <span className={styles.wordmark}>OBSERVA</span>
        {surface ? <span className={styles.surface}>{surface}</span> : null}
      </header>
      <main className={styles.main} aria-label={regionLabel}>
        {children}
      </main>
    </div>
  );
}
