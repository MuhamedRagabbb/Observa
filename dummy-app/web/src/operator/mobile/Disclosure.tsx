import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Icon } from "../../shared/ui/Icon.tsx";
import operatorStyles from "../Operator.module.css";
import { sectionOpen } from "./disclosure.ts";
import styles from "./Disclosure.module.css";
import { usePhoneLayout } from "./use-phone-layout.ts";

export function Disclosure({ id, title, index, summary, locked, defaultOpen, children }: {
  id: string; title: string; index: string; summary: string; locked: boolean; defaultOpen: boolean; children: ReactNode;
}) {
  const phone = usePhoneLayout();
  const [choice, setChoice] = useState<{ open: boolean; locked: boolean } | null>(null);
  const [previousLocked, setPreviousLocked] = useState(locked);
  if (previousLocked !== locked) {
    setPreviousLocked(locked);
    setChoice(null);
  }
  const open = sectionOpen(choice, locked, defaultOpen);
  const toggle = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (phone && !open && content.current?.contains(document.activeElement)) {
      toggle.current?.setAttribute("data-focus-moved", "true");
      toggle.current?.focus({ preventScroll: true });
    }
  }, [phone, open]);

  return <>
    <div className={operatorStyles.sectionHeading}>
      <h2 id={id} className={`${operatorStyles.sectionTitle}${phone ? ` ${styles.heading}` : ""}`}>
        {phone ? <button ref={toggle} type="button" className={styles.toggle} data-disclosure-toggle
          onBlur={(event) => event.currentTarget.removeAttribute("data-focus-moved")}
          aria-expanded={open} aria-controls={`${id}-content`} onClick={() => setChoice({ open: !open, locked })}>
          <span className={styles.text}><span>{title}</span><span className={styles.summary}>{summary}</span></span>
          <Icon name="chevron-down" className={styles.chevron} />
        </button> : title}
      </h2>
      {!phone ? <span className={operatorStyles.sectionIndex} aria-hidden="true">{index}</span> : null}
    </div>
    <div ref={content} id={`${id}-content`} hidden={phone && !open}>{children}</div>
  </>;
}
