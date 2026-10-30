/** Controller evidence only: no clocks, requests or Playwright dependencies. */
import { randomUUID } from "node:crypto";

export type StatusSample = {
  state: string;
  active: { incident_id: string; scenario: string } | null;
  auto_running: boolean;
  auto: { phase?: string; config?: unknown; repeat?: number; step?: number; phase_started_at?: number } | null;
  last_fault: { ended_at: string } | null;
  now: number;
  hostAt?: number;
};

export type CleanupAction = "deactivate-manual" | "stop-cycle";
export type CleanupPlan = { actions: CleanupAction[]; report: string | null };

export class OwnershipLedger {
  private kind: "manual" | "cycle" | null = null;
  private confirmed = false;
  private incidentId: string | null = null;
  private cycle: { config: unknown; startedAt: number } | null = null;
  private progress: StatusSample["auto"] = null;
  private revokedReason: string | null = null;

  begin(kind: "manual" | "cycle") {
    if (this.kind !== null || this.revoked) throw new Error("An ownership attempt is already open or revoked");
    this.kind = kind;
  }

  confirmManual(incidentId: string) {
    if (this.kind !== "manual" || !incidentId) throw new Error("No matching manual ownership attempt");
    this.incidentId = incidentId;
    this.confirmed = true;
  }

  confirmCycle(identity: { config: unknown; startedAt: number }) {
    if (this.kind !== "cycle") throw new Error("No matching cycle ownership attempt");
    this.cycle = structuredClone(identity);
    this.confirmed = true;
  }

  finish() {
    this.kind = null;
    this.confirmed = false;
    this.incidentId = null;
    this.cycle = null;
    this.progress = null;
  }

  get unresolved() { return this.kind !== null && !this.confirmed; }
  get revoked() { return this.revokedReason; }

  revoke(reason: string) {
    if (this.confirmed && !this.revokedReason) this.revokedReason = reason;
  }

  private cycleMismatch(status: StatusSample): string | null {
    const auto = status.auto;
    if (status.state !== "automatic" || !auto) return "running cycle has no automatic status";
    if (auto.config === undefined || !equal(auto.config, this.cycle?.config)) return "cycle config changed or missing";
    for (const key of ["repeat", "step", "phase_started_at"] as const) {
      const current = auto[key];
      const previous = this.progress?.[key];
      if (typeof current !== "number" || !Number.isFinite(current)) return `cycle ${key} missing`;
      if (previous !== undefined && current < previous) return `cycle ${key} decreased`;
    }
    if (auto.phase_started_at! < this.cycle!.startedAt) return "cycle phase predates confirmation identity";
    return null;
  }

  observe(status: StatusSample) {
    if (!this.confirmed || this.revoked) return;
    if (this.kind === "manual") {
      if (status.auto_running) this.revoke("a cycle replaced the owned manual incident");
      else if (!status.active) this.finish();
      else if (status.active.incident_id !== this.incidentId) this.revoke("manual incident id changed");
    } else if (this.kind === "cycle") {
      if (!status.auto_running) this.finish();
      else {
        const reason = this.cycleMismatch(status);
        if (reason) this.revoke(reason);
        else this.progress = structuredClone(status.auto);
      }
    }
  }

  stopCheck(status: StatusSample | null): { allowed: boolean; reason: string | null } {
    const deny = (reason: string) => ({ allowed: false, reason });
    if (this.revoked) return deny(`OWNERSHIP revoked: ${this.revoked}`);
    if (!status) return deny("controller status unreadable");
    if (!this.confirmed) return deny("OWNERSHIP unproven or retired");
    if (this.kind === "manual") {
      if (status.auto_running || status.active?.incident_id !== this.incidentId) return deny("owned manual incident is no longer exclusively active");
    } else {
      if (!status.auto_running) return deny("owned cycle no longer running");
      const reason = this.cycleMismatch(status);
      if (reason) return deny(reason);
    }
    return { allowed: true, reason: null };
  }

  plan(status: StatusSample | null): CleanupPlan {
    if (this.revoked) return { actions: [], report: `OWNERSHIP revoked: ${this.revoked}; nothing was stopped` };
    if (status === null) return {
      actions: [],
      report: `${this.unresolved ? "OWNERSHIP unproven; " : ""}controller status unreadable; nothing was stopped`,
    };
    if (this.unresolved) return { actions: [], report: "OWNERSHIP unproven after an unresolved start; nothing was stopped" };
    if (this.kind === "cycle" && this.confirmed && status.auto_running) {
      // Planning is read-only; callers must observe and pass stopCheck before sending a POST.
      const check = this.stopCheck(status);
      if (!check.allowed) return { actions: [], report: check.reason };
      return { actions: ["stop-cycle"], report: null };
    }
    if (this.kind === "manual" && this.confirmed && !status.auto_running && status.active?.incident_id === this.incidentId) {
      return { actions: ["deactivate-manual"], report: null };
    }
    return { actions: [], report: null };
  }
}

export function recoveryDeadline(lastFault: { ended_at: string }, limitSeconds = 180): number {
  return Date.parse(lastFault.ended_at) / 1000 + limitSeconds;
}

export type RecoveryVerdict = { verdict: "pending" | "idle" | "overran"; elapsedSeconds: number | null };

export function recoveryVerdict(
  samples: readonly StatusSample[],
  { limitSeconds = 180, toleranceSeconds = 5 }: { limitSeconds?: number; toleranceSeconds?: number } = {},
): RecoveryVerdict {
  for (const sample of samples) {
    if (sample.state === "recovering" && sample.last_fault
        && sample.now >= recoveryDeadline(sample.last_fault, limitSeconds) + toleranceSeconds) {
      return { verdict: "overran", elapsedSeconds: sample.now - Date.parse(sample.last_fault.ended_at) / 1000 };
    }
  }
  const latest = samples.at(-1);
  if (!latest?.last_fault) return { verdict: "pending", elapsedSeconds: null };
  return {
    verdict: latest.state === "idle" && latest.active === null && !latest.auto_running ? "idle" : "pending",
    elapsedSeconds: latest.now - Date.parse(latest.last_fault.ended_at) / 1000,
  };
}

/** Structural JSON equality, including array order, independent of object key order. */
function equal(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const left = Object.keys(a).sort();
  const right = Object.keys(b).sort();
  return left.length === right.length && left.every((key, i) => key === right[i]
    && equal((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]));
}

export type GateKind = "start-manual" | "stop-manual" | "start-cycle" | "stop-cycle";
export type MutationRequest = { id: string; method: string; path: string; body: unknown; token: string | null; redirected?: boolean };
export const MUTATIONS: Record<GateKind, string> = {
  "start-manual": "/faults/activate", "stop-manual": "/faults/deactivate",
  "start-cycle": "/faults/auto/start", "stop-cycle": "/faults/auto/stop",
};

export class MutationGate {
  readonly violations: string[] = [];
  private slot: { path: string; body: unknown; token: string } | null = null;
  private readonly authorized = new Set<string>();
  private readonly consumed = new Set<string>();

  arm(kind: GateKind, body: unknown) {
    if (this.slot) throw new Error("Mutation slot already armed");
    const token = randomUUID();
    this.slot = { path: MUTATIONS[kind], body: structuredClone(body), token };
    return token;
  }

  isAuthorized(id: string) { return this.authorized.has(id); }

  decide(request: MutationRequest): { allow: boolean; reason: string | null } {
    if (request.method === "GET" || request.method === "HEAD") return { allow: true, reason: null };
    let reason: string | null = null;
    if (request.method !== "POST") reason = "unsupported mutation method";
    else if (request.redirected) reason = "redirected mutation";
    else if (this.authorized.has(request.id)) reason = "duplicate request id";
    else if (!this.slot) reason = this.consumed.has(request.path) ? "duplicate mutation" : "unsolicited mutation";
    else if (this.slot.path !== request.path) reason = "wrong mutation path";
    else if (!equal(this.slot.body, request.body)) reason = "wrong mutation body";
    else if (this.slot.token !== request.token) reason = "missing or wrong mutation token";
    if (reason) {
      reason = `${reason}: ${request.method} ${request.path} (${request.id})`;
      this.violations.push(reason);
      return { allow: false, reason };
    }
    this.slot = null;
    this.authorized.add(request.id);
    this.consumed.add(request.path);
    return { allow: true, reason: null };
  }
}

export function recoveringUiEvidence(
  samples: readonly StatusSample[], snapshots: readonly { at: number; kind: string | null; title: string }[],
  { pollGapSeconds = 10 }: { pollGapSeconds?: number } = {},
): { verdict: "not-required" | "unobservable" | "satisfied" | "missed" } {
  const recovering = samples.filter((sample) => sample.state === "recovering");
  if (!recovering.length) return { verdict: "not-required" };
  const times = recovering.flatMap((sample) => typeof sample.hostAt === "number" ? [sample.hostAt] : []);
  if (!times.length) return { verdict: "unobservable" };
  const first = Math.min(...times), last = Math.max(...times), gap = pollGapSeconds * 1_000;
  if (snapshots.some((snapshot) => snapshot.kind === "recovering" && snapshot.title === "Waiting for services"
      && snapshot.at >= first - gap && snapshot.at <= last + gap)) return { verdict: "satisfied" };
  return { verdict: last - first <= gap ? "unobservable" : "missed" };
}

export function healthMismatch(
  snapshot: { services: readonly { service: string; state: string }[] } | null,
  rows: readonly { service: string; label: string }[], expected: readonly string[],
): string | null {
  if (!snapshot) return "health snapshot missing";
  if (!equal(snapshot.services.map((row) => row.service).sort(), [...expected].sort())) return "snapshot services differ from expected services";
  const names = rows.map((row) => row.service);
  if (new Set(names).size !== names.length) return "duplicate table service";
  for (const name of names) if (!expected.includes(name)) return `extra table service ${name}`;
  for (const service of snapshot.services) {
    const row = rows.find((entry) => entry.service === service.service);
    if (!row) return `missing table service ${service.service}`;
    const label = service.state.charAt(0).toUpperCase() + service.state.slice(1);
    if (row.label !== label) return `${service.service} label differs from ${label}`;
  }
  return null;
}

export function quietWindow(samples: readonly StatusSample[], heads: readonly string[], minSeconds = 4): { quiet: boolean; reason: string | null } {
  const deny = (reason: string) => ({ quiet: false, reason });
  if (samples.length < 2) return deny("insufficient status samples");
  if (samples.at(-1)!.now - samples[0]!.now < minSeconds) return deny("quiet window too short");
  if (samples.some((sample) => sample.state !== "idle" || sample.active || sample.auto_running || sample.auto)) return deny("non-idle quiet window");
  if (samples.some((sample) => !equal(sample.last_fault, samples[0]!.last_fault))) return deny("last_fault changed during quiet window");
  if (!heads.length || heads.some((head) => head !== heads[0])) return deny("activity head absent or changed");
  return { quiet: true, reason: null };
}

export function foreignActivity(entries: readonly { at: string; event: string }[], since: string, allowed: readonly string[]): string[] {
  return entries.filter((entry) => entry.at > since && !allowed.includes(entry.event))
    .map((entry) => `foreign activity ${entry.event} at ${entry.at}`);
}

export function observedPhases(samples: readonly StatusSample[]): string[] {
  const phases: string[] = [];
  for (const sample of samples) {
    const phase = sample.state === "automatic" && sample.auto?.phase ? `automatic:${sample.auto.phase}` : sample.state;
    if (phases.at(-1) !== phase) phases.push(phase);
  }
  return phases;
}
