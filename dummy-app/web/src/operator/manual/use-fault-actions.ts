import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { postCycleStart, postCycleStop } from "../cycle/cycle-actions.ts";
import type { CycleRequest } from "../cycle/cycle-form.ts";
import type { ScenarioId } from "../fault-contract.ts";
import type { StatusReading } from "../status/controller-status.ts";
import { postStart, postStop, type ActionOutcome } from "./fault-actions.ts";
import type { ActivationRequest } from "./fault-form.ts";

type ActionGroup = "start" | "stop";
type ActionKind = ActionGroup | "cycle-start" | "cycle-stop";
export type FaultActionBusy = ActionKind | null;
const cooldownUntil: Record<ActionGroup, number> = { start: 0, stop: 0 };
let stopsSent = 0;

export function resetFaultActionCooldown() {
  cooldownUntil.start = 0;
  cooldownUntil.stop = 0;
  stopsSent = 0;
}

// Exclude the ticking clock: only a change the operator can see dismisses an outcome.
const visibleState = (reading: StatusReading | undefined) => reading ? JSON.stringify([
  reading.status.state, reading.status.active?.incident_id, reading.status.active?.scenario,
  reading.status.active?.status, reading.status.auto_running, reading.status.auto?.phase,
  JSON.stringify(reading.status.auto?.config),
]) : undefined;

/** Stops can interrupt starts; only the controller's acknowledged answer can seed status. */
export function useFaultActions({ refresh }: { refresh: () => void | Promise<unknown> }) {
  const client = useQueryClient();
  const pending = useRef<Record<ActionGroup, { request: AbortController; kind: ActionKind } | null>>({ start: null, stop: null });
  const mounted = useRef(true);
  const [busy, setBusy] = useState<FaultActionBusy>(null);
  const [startOutcome, setStartOutcome] = useState<ActionOutcome | null>(null);
  const [cycleStartOutcome, setCycleStartOutcome] = useState<ActionOutcome | null>(null);
  const [stopOutcome, setStopOutcome] = useState<ActionOutcome | null>(null);
  const answeredState = useRef<string | undefined>(undefined);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      pending.current.start?.request.abort();
      pending.current.stop?.request.abort();
      pending.current = { start: null, stop: null };
    };
  }, []);

  useEffect(() => client.getQueryCache().subscribe((event) => {
    if (pending.current.start || pending.current.stop || event.type !== "updated" || event.action.type !== "success"
      || event.query.queryKey.length !== 1 || event.query.queryKey[0] !== "controller-status") return;
    const reading = event.query.state.data as StatusReading | undefined;
    if (!reading) return;
    const state = visibleState(reading);
    // An outcome describes the state the controller reported right after the action; it goes away
    // when the controller later reports something else. Read-back contradictions are checked below.
    const clear = (outcome: ActionOutcome | null) => (state !== answeredState.current ? null : outcome);
    setStartOutcome(clear);
    setCycleStartOutcome(clear);
    setStopOutcome(clear);
  }), [client]);

  async function run(kind: ActionKind, send: ((signal: AbortSignal) => Promise<ActionOutcome>) | null, isFresh?: () => boolean) {
    const group: ActionGroup = kind === "start" || kind === "cycle-start" ? "start" : "stop";
    const setOutcome = kind === "start" ? setStartOutcome : kind === "cycle-start" ? setCycleStartOutcome : setStopOutcome;
    // State alone cannot guard two presses in the same React turn.
    if (!mounted.current || pending.current[group] || (group === "start" && pending.current.stop)
      || performance.now() < cooldownUntil[group]) return;
    const stale = group === "start" && isFresh?.() === false;
    const posting = send !== null && !stale;
    const request = new AbortController();
    pending.current[group] = { request, kind };
    setBusy(pending.current.stop?.kind ?? kind);
    if (posting) {
      setStartOutcome(null);
      setCycleStartOutcome(null);
      setStopOutcome(null);
    }
    if (posting && group === "stop") stopsSent++;
    const stopsAtSend = stopsSent;
    let outcome: ActionOutcome = !posting ? {
      kind: "refused", code: "stale_status",
      message: "The controller’s state is being re-read. Try again in a moment.",
    } : await send(request.signal);
    if (!mounted.current || pending.current[group]?.request !== request) return;
    if (posting && group === "start" && stopsAtSend !== stopsSent) {
      outcome = {
        kind: "unconfirmed",
        message: "This start was answered after a stop was pressed, so it isn’t shown as confirmed. The status above is the controller’s current state.",
      };
    }
    if (outcome.kind === "confirmed") {
      client.setQueryData<StatusReading>(["controller-status"], { status: outcome.status, receivedAt: performance.now() });
    }
    setOutcome(outcome);
    // A stalled read-back must release its group so the operator can try again.
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        Promise.resolve().then(refresh),
        new Promise<void>((resolve) => { timer = setTimeout(resolve, 6000); }),
      ]).catch(() => undefined);
    } finally {
      clearTimeout(timer);
    }
    if (!mounted.current || pending.current[group]?.request !== request) return outcome;
    const reading = client.getQueryData<StatusReading>(["controller-status"]);
    answeredState.current = visibleState(reading);
    if (outcome.kind === "confirmed" && reading) {
      const status = reading.status;
      const contradicted = kind === "start"
        ? status.state !== "manual" || status.active?.incident_id !== outcome.status.active?.incident_id
        : kind === "cycle-start" ? status.state !== "automatic" || !status.auto_running || status.auto === null
          || JSON.stringify(status.auto.config) !== JSON.stringify(outcome.status.auto?.config)
        : (status.state !== "idle" && status.state !== "recovering") || status.active !== null || status.auto_running;
      if (contradicted) setOutcome(null);
    }
    if (posting) cooldownUntil[group] = performance.now() + 800;
    pending.current[group] = null;
    setBusy(pending.current.stop?.kind ?? pending.current.start?.kind ?? null);
    return outcome;
  }

  return {
    busy, startOutcome, cycleStartOutcome, stopOutcome,
    start: (request: ActivationRequest, isFresh?: () => boolean) => run("start", (signal) => postStart(request, signal), isFresh),
    stop: (scenario: ScenarioId) => run("stop", (signal) => postStop(scenario, signal)),
    startCycle: (request: CycleRequest, isFresh?: () => boolean) => run("cycle-start", (signal) => postCycleStart(request, signal), isFresh),
    stopCycle: () => run("cycle-stop", (signal) => postCycleStop(signal)),
    // An unavailable button may request a fresh read, never a POST.
    refreshIfStale: (kind: ActionKind) => run(kind, null),
    clearStart: () => setStartOutcome(null),
    clearCycleStart: () => setCycleStartOutcome(null),
  };
}
