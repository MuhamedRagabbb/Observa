import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import styles from "./CrossAppLink.module.css";
import { crossAppUrl, fetchUiConfig } from "./ui-config.ts";

type CrossAppLinkProps = {
  target: "store" | "control";
  /** Replaces the default underlined style (the storefront header sets it in small capitals). */
  className?: string;
  children: ReactNode;
};

/**
 * A plain link to the other app (storefront or operator) on the same host. It is shown
 * only once /ui/config has answered, so the page never shows a guessed address.
 */
export function CrossAppLink({ target, className, children }: CrossAppLinkProps) {
  const { data: config } = useQuery({ queryKey: ["ui-config"], queryFn: fetchUiConfig, staleTime: Infinity });
  if (!config) return null;
  return (
    <a className={className ?? styles.link} href={crossAppUrl(config, target)}>
      {children}
      <span className={styles.arrow} aria-hidden="true">
        ↗
      </span>
    </a>
  );
}
