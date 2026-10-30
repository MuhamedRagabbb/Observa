// Service health is current only while the controller can be reached. Missing answers stay
// unknown; a usable snapshot decides which services are shown.
import type { components } from "../../api/generated/controller.ts";
import { formatTime } from "../status/experiment.ts";

export const SERVICE_NAMES = ["api-gateway", "orders-service", "inventory-service", "payments-service"] as const;

type HealthSnapshot = components["schemas"]["HealthSnapshot"];
type ServiceHealth = components["schemas"]["ServiceHealth"];

const MARKERS = {
  healthy: { label: "Healthy", shape: "filled", tone: "success" },
  degraded: { label: "Degraded", shape: "half", tone: "ink" },
  unavailable: { label: "Unavailable", shape: "hollow", tone: "fault" },
  unknown: { label: "Unknown", shape: "hollow", tone: "muted" },
} as const;

const REASONS: Record<string, string> = {
  probe_failed: "Health probe failed",
  high_error_ratio: "High error ratio",
  high_latency: "High latency",
  stale_metrics: "No recent traffic",
  no_metrics: "No metrics for this service",
  metrics_unavailable: "Metrics unavailable",
};

const reasonText = (code: string) => Object.hasOwn(REASONS, code) ? REASONS[code] : code.charAt(0).toUpperCase() + code.slice(1).replaceAll("_", " ");

export type HealthRow = {
  service: string;
  state: ServiceHealth["state"];
  label: string;
  shape: "filled" | "half" | "hollow";
  tone: "success" | "ink" | "fault" | "muted";
  reasons: string | null;
  lastCheck: string;
};

export function healthView({ snapshot, unreachable, timeZone }: { snapshot: HealthSnapshot | null; unreachable: boolean; timeZone?: string }): { rows: HealthRow[]; notice: string | null } {
  if (unreachable || !snapshot) {
    return {
      rows: SERVICE_NAMES.map((service) => ({ service, state: "unknown", ...MARKERS.unknown, reasons: unreachable ? "Controller not reachable" : null, lastCheck: "—" })),
      notice: unreachable ? "Can’t reach the controller, so service health is unknown." : null,
    };
  }
  return {
    rows: snapshot.services.map((service) => ({
      service: service.service,
      state: service.state,
      ...MARKERS[service.state],
      reasons: service.reasons.length ? service.reasons.map(reasonText).join(" · ") : null,
      lastCheck: formatTime(service.checked_at, timeZone) ?? "—",
    })),
    notice: snapshot.metrics === "unavailable"
      ? "Metrics are unavailable, so only services the controller can check directly are judged."
      : snapshot.metrics === "unknown" ? "Metrics have not been read yet." : null,
  };
}
