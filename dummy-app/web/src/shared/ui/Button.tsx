import type { ButtonHTMLAttributes, MouseEvent, ReactNode } from "react";
import { Link, type LinkProps } from "react-router";
import styles from "./Button.module.css";
import { Icon, type IconName } from "./Icon.tsx";

type Common = {
  /** The glyph in the icon block at the start of the button. */
  icon: IconName;
  /** "primary" is the ink button; "secondary" the light outlined one. */
  variant?: "primary" | "secondary";
  /** "large" is the 60px page action (Place order); "medium" the 48px row action (Add to cart). */
  size?: "medium" | "large";
  /** Stretch to the container's width. */
  block?: boolean;
  children: ReactNode;
};

function Body({ icon, loading, children }: { icon: IconName; loading?: boolean; children: ReactNode }) {
  return (
    <>
      <span className={styles.fill} aria-hidden="true" />
      <span className={styles.iconBlock}>
        {loading ? <span className={styles.spinner} aria-hidden="true" /> : <Icon name={icon} />}
      </span>
      <span className={styles.label}>{children}</span>
    </>
  );
}

const classes = (size: Common["size"], block: boolean | undefined) =>
  [styles.button, size === "large" ? styles.large : styles.medium, block ? styles.block : ""].join(" ");

type ButtonProps = Common &
  Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "className" | "type"> & {
    type?: "button" | "submit";
    /**
     * A request is in flight: the button reports busy and ignores presses, but keeps focus so
     * keyboard and screen-reader users do not lose their place.
     */
    loading?: boolean;
  };

export function Button({
  icon,
  variant = "primary",
  size = "medium",
  block,
  type = "button",
  loading = false,
  onClick,
  children,
  ...rest
}: ButtonProps) {
  const handleClick = (event: MouseEvent<HTMLButtonElement>) => {
    if (loading) {
      event.preventDefault();
      return;
    }
    onClick?.(event);
  };
  return (
    <button
      {...rest}
      type={type}
      className={classes(size, block)}
      data-variant={variant}
      aria-busy={loading || undefined}
      aria-disabled={loading || undefined}
      onClick={handleClick}
    >
      <Body icon={icon} loading={loading}>
        {children}
      </Body>
    </button>
  );
}

type ButtonLinkProps = Common & Pick<LinkProps, "to" | "state" | "replace">;

/** Looks like a Button but navigates, so it is a link for assistive tech and the browser. */
export function ButtonLink({ icon, variant = "primary", size = "medium", block, children, ...link }: ButtonLinkProps) {
  return (
    <Link {...link} className={classes(size, block)} data-variant={variant}>
      <Body icon={icon}>{children}</Body>
    </Link>
  );
}
