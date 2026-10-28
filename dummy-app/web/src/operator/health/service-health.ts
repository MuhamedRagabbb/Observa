// A failed read keeps the last snapshot available to callers, marked unreachable.
import { useQuery } from "@tanstack/react-query";
import { createControllerClient } from "../../api/controller-client.ts";
import type { components } from "../../api/generated/controller.ts";
import { ControllerUnreachable, fetchControllerRead, isRecord, parseControllerJson } from "../status/controller-status.ts";

export type HealthSnapshot = components["schemas"]["HealthSnapshot"];
export const HEALTH_POLL_MS = 5000;
export const HEALTH_TIMEOUT_MS = 5000;

const STATES = new Set(["healthy", "degraded", "unavailable", "unknown"]);
const METRICS = new Set(["available", "unavailable", "unknown"]);

export function parseHealthSnapshot(body: unknown): HealthSnapshot {
  const ok =
    isRecord(body) &&
    (body.checked_at === null || typeof body.checked_at === "string") &&
    typeof body.metrics === "string" && METRICS.has(body.metrics) &&
    Array.isArray(body.services) &&
    body.services.every((service: unknown) =>
      isRecord(service) &&
      typeof service.service === "string" &&
      typeof service.state === "string" && STATES.has(service.state) &&
      typeof service.checked_at === "string" &&
      Array.isArray(service.reasons) && service.reasons.every((reason: unknown) => typeof reason === "string"));
  if (!ok) throw new ControllerUnreachable("invalid_response");
  return body as HealthSnapshot;
}

export function fetchServiceHealth(signal?: AbortSignal): Promise<HealthSnapshot> {
  return fetchControllerRead(
    (signal) => createControllerClient().GET("/services/health", { signal, parseAs: "text" }),
    (body) => parseHealthSnapshot(parseControllerJson(body)),
    signal,
    HEALTH_TIMEOUT_MS,
  );
}

export function useServiceHealth(): { snapshot: HealthSnapshot | null; unreachable: boolean } {
  const query = useQuery({
    queryKey: ["service-health"],
    queryFn: ({ signal }) => fetchServiceHealth(signal),
    refetchInterval: HEALTH_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    retry: false,
  });
  return { snapshot: query.data ?? null, unreachable: query.isError };
}
