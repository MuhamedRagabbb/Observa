// Deterministic controller answers for the Operator Dashboard's mocked browser tests (P5).
// Typed by the generated controller API types; the controls per state and the health service
// names come from controller-facts.json, which pytest checks against the controller itself
// (tests/unit/test_operator_fixtures.py). Times are on the controller's clock: CONTROLLER_NOW.
import type { components } from "../../src/api/generated/controller.ts";
import facts from "./controller-facts.json" with { type: "json" };

export type ControllerStatus = components["schemas"]["ControllerStatus"];
export type OperatorIncident = components["schemas"]["OperatorIncident"];
export type AutoStatus = components["schemas"]["AutoStatus"];
export type HealthSnapshot = components["schemas"]["HealthSnapshot"];
export type ServiceHealth = components["schemas"]["ServiceHealth"];
export type Activity = components["schemas"]["Activity"];
export type ActivityEntry = components["schemas"]["ActivityEntry"];

export const STATES = ["idle", "manual", "automatic", "recovering", "unknown"] as const;
export type ControllerState = (typeof STATES)[number];

/** The controller's clock in the fixtures (epoch seconds): 2026-10-28T14:00:00Z. */
export const CONTROLLER_NOW = Date.UTC(2026, 9, 28, 14, 0, 0) / 1000;
const iso = (seconds: number) => new Date(seconds * 1000).toISOString();

/** F2 at 90 %, HTTP 503, 300 s, started 78 s ago: 03:42 left (the manual-fault artboard). */
function incident(status: string): OperatorIncident {
  return {
    incident_id: "inc-00000000-0000-4000-8000-000000000001",
    scenario: "payments_errors",
    status,
    started_at: iso(CONTROLLER_NOW - 78),
    acknowledged_at: iso(CONTROLLER_NOW - 77),
    expires_at: CONTROLLER_NOW + 222,
    parameters: { failure_rate: 0.9, status_code: 503 },
  };
}

/** Repeat 2 of 3, fault phase of F2 (step 4 of 6), 01:18 left; F1 and F2, shuffled, seed 7341. */
function cycle(): AutoStatus {
  return {
    phase: "fault",
    repeat: 2,
    repeats: 3,
    step: 4,
    steps: 6,
    scenario: "payments_errors",
    phase_started_at: CONTROLLER_NOW - 42,
    phase_ends_at: CONTROLLER_NOW + 78,
    seed: 7341,
    shuffle: true,
    config: {
      seed: 7341,
      scenarios: ["db_connection_exhaustion", "payments_errors"],
      shuffle: true,
      warmup_seconds: 60,
      duration_seconds: 120,
      recovery_seconds: 180,
      repeat: 3,
    },
  };
}

/** What GET /faults/status answers in each state; `overrides` replaces whole fields. */
export function controllerStatus(state: ControllerState, overrides: Partial<ControllerStatus> = {}): ControllerStatus {
  return {
    state,
    active: state === "manual" ? incident("active") : state === "unknown" ? incident("deactivation_unconfirmed") : state === "automatic" ? { ...incident("active"), expires_at: CONTROLLER_NOW + 78 } : null,
    auto_running: state === "automatic",
    auto: state === "automatic" ? cycle() : null,
    last_fault: state === "recovering" ? { scenario: "payments_errors", ended_at: iso(CONTROLLER_NOW - 9), end_reason: "manual" } : null,
    controls: structuredClone(facts.controls[state]),
    now: CONTROLLER_NOW,
    ...overrides,
  };
}

/** GET /services/health: every service the controller reports, healthy unless overridden. */
export function healthSnapshot(overrides: Record<string, Partial<ServiceHealth>> = {}, metrics: HealthSnapshot["metrics"] = "available"): HealthSnapshot {
  const checkedAt = iso(CONTROLLER_NOW - 2);
  return {
    checked_at: checkedAt,
    metrics,
    services: facts.health_services.map((service) => ({
      service,
      state: "healthy",
      checked_at: checkedAt,
      probe: service === "orders-service" || service === "payments-service" ? { ok: true, status_code: 200, latency_ms: 12 } : null,
      error_ratio: 0,
      mean_latency_ms: 40,
      p95_latency_ms: 120,
      reasons: [],
      ...overrides[service],
    })),
  };
}

/** One activity entry, `secondsAfterStart` after the session's start (CONTROLLER_NOW - 120 s). */
export function activityEntry(event: string, secondsAfterStart: number, extras: Record<string, unknown> = {}): ActivityEntry {
  return { at: iso(CONTROLLER_NOW - 120 + secondsAfterStart), event, ...extras };
}

/** GET /activity: entries given oldest first are answered newest first, as the controller does. */
export function activity(entries: ActivityEntry[], capacity = 200): Activity {
  return { capacity, entries: [...entries].sort((a, b) => b.at.localeCompare(a.at)) };
}
