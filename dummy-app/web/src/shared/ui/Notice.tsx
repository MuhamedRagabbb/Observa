import { useEffect, useState, type ReactNode } from "react";
import { Icon } from "./Icon.tsx";
import styles from "./Notice.module.css";

type NoticeProps = {
  /** "info" for standing facts (demo store); "ring" for a problem to act on (unavailable item). */
  icon: "info" | "ring";
  /** Announce politely: only for a notice that appears in response to a change. */
  live?: boolean;
  /** An action beside the message, such as a "Remove" TextButton. */
  action?: ReactNode;
  children: ReactNode;
};

export function Notice({ icon, live = false, action, children }: NoticeProps) {
  // A live notice mounts its region empty and adds the message a moment later: screen readers
  // announce changes inside a region that already exists, not a region that arrives with text.
  const [shown, setShown] = useState(!live);
  useEffect(() => {
    if (!live) return;
    const timer = setTimeout(() => setShown(true), 50);
    return () => clearTimeout(timer);
  }, [live]);
  return (
    <div className={styles.notice}>
      {icon === "info" ? <Icon name="info" className={styles.info} /> : <span className={styles.ring} aria-hidden="true" />}
      <p className={styles.message} {...(live ? { role: "status", "aria-live": "polite" as const } : {})}>
        {shown ? children : null}
      </p>
      {action ? <div className={styles.action}>{action}</div> : null}
    </div>
  );
}
