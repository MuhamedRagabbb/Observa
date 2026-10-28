import { toolUrl, type UiConfig } from "../shared/ui-config.ts";

export function observabilityLinks(config: UiConfig, page: URL | Location = window.location) {
  return [
    { id: "grafana", name: "Grafana", caption: "Dashboards", href: toolUrl(config, "grafana", page) },
    { id: "prometheus", name: "Prometheus", caption: "Metrics", href: toolUrl(config, "prometheus", page) },
    { id: "loki", name: "Loki", caption: "Logs", href: toolUrl(config, "grafana", page, "/explore") },
    { id: "jaeger", name: "Jaeger", caption: "Traces", href: toolUrl(config, "jaeger", page) },
  ];
}
