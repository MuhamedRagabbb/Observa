import { useQuery } from "@tanstack/react-query";
import { fetchUiConfig } from "../../shared/ui-config.ts";
import { observabilityLinks } from "../links.ts";
import styles from "../Operator.module.css";

export function ObservabilityLinks() {
  const { data: config } = useQuery({ queryKey: ["ui-config"], queryFn: fetchUiConfig, staleTime: Infinity });
  if (!config) return null;
  return (
    <ul className={styles.observabilityTools} aria-label="Observability tools">
      {observabilityLinks(config).map(({ id, name, caption, href }) => (
        <li key={id}>
          <a className={styles.toolCard} href={href} aria-label={`${name}, ${caption}`}>
            <span className={styles.toolName}>{name}</span>
            <span className={styles.toolArrow} aria-hidden="true">↗</span>
            <span className={styles.toolCaption}>{caption}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}
