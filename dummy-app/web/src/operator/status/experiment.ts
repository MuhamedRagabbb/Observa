// What the experiment banner says (Operator artboards: idle, manual fault active, automatic
// cycle, recovering, fault status unknown, controller disconnected). Pure: it reads only the
// controller's last answer, whether the latest request reached it, and how many seconds have
// passed since that answer arrived. Countdowns use the controller's clock (`now`, `expires_at`,
// `phase_ends_at`), never the browser's, and never run past zero or while disconnected.
import type { components } from "../../api/generated/controller.ts";
import type { ScenarioId } from "../fault-contract.ts";
import { faultSummary, SCENARIO_LABELS, scenarioCode } from "../scenarios.ts";

export type ControllerStatus = components["schemas"]["ControllerStatus"];
export type Connection = "connecting" | "connected" | "disconnected";

export type Banner = {
  kind: "connecting" | "idle" | "manual" | "automatic" | "recovering" | "unknown" | "disconnected";
  eyebrow: string;
  title: string;
  description: string;
  /** A running countdown: the time left, what it counts, and the share already elapsed (0 to 1). */
  timer: { text: string; caption: string; progress: number } | null;
  /** Shown in place of a countdown. */
  timerNote: string | null;
  /** A state that needs the operator's attention (the artboards' red frame). */
  alert: boolean;
  /** The banner's own read-only action. Fault actions (Stop) arrive with the controls (P5-B, P5-C). */
  action: "check-status" | "retry" | null;
  /** Said once by screen readers when the state changes; never the ticking countdown. */
  announcement: string;
};

export type ExperimentInput = {
  connection: Connection;
  /** The controller's last answer, kept while it is unreachable. */
  status: ControllerStatus | null;
  /** Seconds since that answer arrived (a monotonic clock). */
  elapsed: number;
  /** For tests; the page uses the browser's time zone. */
  timeZone?: string;
};

/** Time left: 222 → "03:42"; rounded up, so it reads 00:00 only once nothing is left; "1:02:05" past an hour. */
export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.ceil(seconds - 1e-6));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = String(total % 60).padStart(2, "0");
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${String(minutes).padStart(2, "0")}:${rest}`;
}

/** A duration as "5:00" (minutes:seconds). */
const formatSpan = (seconds: number) => {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
};

export function formatTime(iso: string | null | undefined, timeZone?: string): string | null {
  if (!iso) return null;
  const time = Date.parse(iso);
  if (Number.isNaN(time)) return null;
  return new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23", ...(timeZone ? { timeZone } : {}) }).format(time);
}

function countdown(endsAt: number, startedAt: number, status: ControllerStatus, elapsed: number, caption: string) {
  const left = Math.max(0, endsAt - status.now - elapsed);
  const span = Math.max(endsAt - startedAt, 0);
  const progress = span > 0 ? Math.min(1, Math.max(0, (span - left) / span)) : 0;
  return { text: formatClock(left), caption, progress };
}

const known = (scenario: string | null | undefined): scenario is ScenarioId => !!scenario && scenario in SCENARIO_LABELS;
/** A scenario this page does not know (a newer controller) is named as sent. */
const label = (scenario: string | null | undefined) => (known(scenario) ? SCENARIO_LABELS[scenario] : scenario || "a fault");

const LAST_CONFIRMED: Record<string, string> = {
  idle: "idle",
  manual: "a running fault",
  automatic: "a running automatic cycle",
  recovering: "recovering after a fault",
  unknown: "an unconfirmed fault status",
};

export function experimentView({ connection, status, elapsed, timeZone }: ExperimentInput): Banner {
  if (connection === "disconnected") {
    const last = status ? LAST_CONFIRMED[status.state] : undefined;
    return {
      kind: "disconnected",
      eyebrow: "Controller",
      title: "Unreachable",
      description: `Can’t reach the local controller. Live state is unknown; ${last ? `the last confirmed state was ${last}` : "nothing has been confirmed yet"}.`,
      timer: null,
      timerNote: "Unavailable",
      alert: true,
      action: "retry",
      announcement: "Controller unreachable.",
    };
  }
  if (connection === "connecting" || !status) {
    return {
      kind: "connecting",
      eyebrow: "Controller",
      title: "Connecting",
      description: "Reading the controller’s current state.",
      timer: null,
      timerNote: "Unavailable",
      alert: false,
      action: null,
      announcement: "Connecting to the controller.",
    };
  }

  switch (status.state) {
    case "manual": {
      const active = status.active;
      if (!active) break;
      const scenario = active.scenario;
      const started = formatTime(active.started_at, timeZone);
      const startedAt = Date.parse(active.started_at) / 1000;
      const hasStart = !Number.isNaN(startedAt);
      const startedText = started ? `Started ${started}` : "";
      return {
        kind: "manual",
        eyebrow: known(scenario) ? `Manual fault · ${scenarioCode(scenario)}` : "Manual fault",
        title: label(scenario),
        description: known(scenario)
          ? `${startedText ? `${startedText} · ` : ""}${faultSummary(scenario, active.parameters)}`
          : `${startedText || "Started at an unknown time"}.`,
        timer: countdown(active.expires_at, hasStart ? startedAt : active.expires_at, status, elapsed, hasStart ? `Remaining of ${formatSpan(active.expires_at - startedAt)}` : "Remaining"),
        timerNote: null,
        alert: true,
        action: null,
        announcement: `Manual fault active: ${label(scenario)}.`,
      };
    }
    case "automatic": {
      const auto = status.auto;
      if (!auto) {
        // Answered while a stop waits for a target: the cycle is ending and its details are gone.
        return {
          kind: "automatic",
          eyebrow: "Automatic cycle",
          title: "Stopping the cycle",
          description: "Stopping the cycle and any fault it started.",
          timer: null,
          timerNote: "No active timer",
          alert: true,
          action: null,
          announcement: "Automatic cycle: stopping.",
        };
      }
      const order = `${auto.shuffle ? "shuffled" : "in order"} · seed ${auto.seed}`;
      const phase = {
        warmup: { title: "Warmup", description: `Warmup before the first fault · ${order}` },
        fault: { title: label(auto.scenario), description: `Fault phase · ${order}` },
        recovery: { title: "Recovery", description: `Recovery after ${label(auto.scenario)} · ${order}` },
        stopping: { title: "Stopping the cycle", description: "Stopping the cycle and any fault it started." },
      }[auto.phase];
      return {
        kind: "automatic",
        eyebrow: `Automatic cycle · Repeat ${auto.repeat} of ${auto.repeats}`,
        title: phase.title,
        description: phase.description,
        timer: auto.phase_ends_at == null ? null : countdown(auto.phase_ends_at, auto.phase_started_at, status, elapsed, "Left in this phase"),
        timerNote: auto.phase_ends_at == null ? "No active timer" : null,
        alert: true,
        action: null,
        announcement: `Automatic cycle, repeat ${auto.repeat} of ${auto.repeats}: ${phase.title}.`,
      };
    }
    case "recovering": {
      const stopped = formatTime(status.last_fault?.ended_at, timeZone);
      return {
        kind: "recovering",
        eyebrow: "Recovering",
        title: "Waiting for services",
        description: `${stopped ? `Fault stopped at ${stopped}. ` : ""}Controls unlock when health checks pass.`,
        timer: null,
        timerNote: "No active timer",
        alert: false,
        action: null,
        announcement: "Recovering: waiting for services.",
      };
    }
    case "unknown": {
      const description =
        status.active?.status === "deactivation_unconfirmed"
          ? "The stop request wasn’t confirmed. The fault may still be running."
          : status.active?.status === "activation_unconfirmed"
            ? "The start request wasn’t confirmed. The fault may be running."
            : "The controller couldn’t confirm whether a fault is running.";
      return {
        kind: "unknown",
        eyebrow: "Fault status",
        title: "Unknown",
        description,
        timer: null,
        timerNote: "Unavailable",
        alert: true,
        action: "check-status",
        announcement: "Fault status unknown.",
      };
    }
  }

  return {
    kind: "idle",
    eyebrow: "Experiment",
    title: "Idle",
    description: "No fault is running.",
    timer: null,
    timerNote: "No active timer",
    alert: false,
    action: null,
    announcement: "Idle. No fault is running.",
  };
}
