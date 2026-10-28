import { useQuery } from "@tanstack/react-query";
import { createControllerClient } from "../../api/controller-client.ts";
import type { components } from "../../api/generated/controller.ts";
import { ControllerUnreachable, fetchControllerRead, isNumber, isRecord, parseControllerJson } from "../status/controller-status.ts";
import { sessionActivity, type ActivityItem } from "./session-activity.ts";

export type Activity = components["schemas"]["Activity"];
export const ACTIVITY_POLL_MS = 5000;
export const ACTIVITY_TIMEOUT_MS = 5000;

export function parseActivity(body: unknown): Activity {
  const ok =
    isRecord(body) &&
    isNumber(body.capacity) &&
    Array.isArray(body.entries) &&
    body.entries.every((entry: unknown) => isRecord(entry) && typeof entry.at === "string" && typeof entry.event === "string");
  if (!ok) throw new ControllerUnreachable("invalid_response");
  return body as Activity;
}

export function fetchActivity(signal?: AbortSignal): Promise<Activity> {
  return fetchControllerRead(
    (signal) => createControllerClient().GET("/activity", { signal, params: { query: { limit: 200 } }, parseAs: "text" }),
    (body) => parseActivity(parseControllerJson(body)),
    signal,
    ACTIVITY_TIMEOUT_MS,
  );
}

export function useSessionActivity(sessionStart: number | null): { items: ActivityItem[]; unreachable: boolean } {
  const query = useQuery({
    queryKey: ["session-activity"],
    queryFn: ({ signal }) => fetchActivity(signal),
    enabled: sessionStart !== null,
    refetchInterval: sessionStart === null ? false : ACTIVITY_POLL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    retry: false,
  });
  return {
    items: sessionStart === null ? [] : sessionActivity({ entries: query.data?.entries ?? [], sessionStart }),
    unreachable: sessionStart !== null && query.isError,
  };
}
