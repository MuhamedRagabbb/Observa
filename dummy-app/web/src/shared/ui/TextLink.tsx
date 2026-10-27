import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link, type LinkProps } from "react-router";
import { Icon, type IconName } from "./Icon.tsx";
import styles from "./TextLink.module.css";

type TextLinkProps = Pick<LinkProps, "to" | "state" | "replace" | "aria-label"> & {
  icon?: IconName;
  children: ReactNode;
};

/** An underlined in-app link, such as "Return to cart". */
export function TextLink({ icon, children, ...link }: TextLinkProps) {
  return (
    <Link {...link} className={styles.text}>
      {icon ? <Icon name={icon} className={styles.icon} /> : null}
      <span className={styles.label}>{children}</span>
    </Link>
  );
}

type TextButtonProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "type" | "className"> & {
  icon?: IconName;
  children: ReactNode;
};

/** Looks like a TextLink but acts on the page (such as "Remove"), so it is a button. */
export function TextButton({ icon, children, ...rest }: TextButtonProps) {
  return (
    <button {...rest} type="button" className={`${styles.text} ${styles.button}`}>
      {icon ? <Icon name={icon} className={styles.icon} /> : null}
      <span className={styles.label}>{children}</span>
    </button>
  );
}
