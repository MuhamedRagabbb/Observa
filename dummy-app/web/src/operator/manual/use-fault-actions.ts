import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { ScenarioId } from "../fault-contract.ts";
import type { StatusReading } from "../status/controller-status.ts";
import { postStart, postStop, type ActionOutcome } from "./fault-actions.ts";
import type { ActivationRequest } from "./fault-form.ts";

// Exclude the ticking clock: only a change the operator can see dismisses an outcome.
const visibleState = (reading: StatusReading | undefined) => reading ? JSON.stringify([
  reading.status.state, reading.status.active?.incident_id, reading.status.active?.scenario,
  reading.status.active?.status, reading.status.auto_running, reading.status.auto?.phase,
]) : undefined;

/** One explicit POST at a time; only the controller's acknowledged answer can seed status. */
export function useFaultActions({ refresh, isFresh }: { refresh: () => void | Promise<unknown>; isFresh?: () => boolean }) {
  const client = useQueryClient();
  const pending = useRef<AbortController | null>(null);
  const cooldownUntil = useRef(0);
  const mounted = useRef(true);
  const [busy, setBusy] = useState<"start" | "stop" | null>(null);
  const [startOutcome, setStartOutcome] = useState<ActionOutcome | null>(null);
  const [stopOutcome, setStopOutcome] = useState<ActionOutcome | null>(null);
  const answeredState = useRef<string | undefined>(undefined);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      pending.current?.abort();
      pending.current = null;
    };
  }, []);

  useEffect(() => client.getQueryCache().subscribe((event) => {
    if (pending.current || event.type !== "updated" || event.action.type !== "success"
      || event.query.queryKey.length !== 1 || event.query.queryKey[0] !== "controller-status") return;
    const reading = event.query.state.data as StatusReading | undefined;
    if (!reading) return;
    const state = visibleState(reading);
    // An outcome describes the state the controller reported right after the action; it goes away
    // when the controller later reports something else. Read-back contradictions are checked below.
    const clear = (outcome: ActionOutcome | null) => (state !== answeredState.current ? null : outcome);
    setStartOutcome(clear);
    setStopOutcome(clear);
  }), [client]);

  async function run(kind: "start" | "stop", send: ((signal: AbortSignal) => Promise<ActionOutcome>) | null) {
    // State alone cannot guard two presses in the same React turn.
    if (!mounted.current || pending.current || performance.now() < cooldownUntil.current) return;
    const stale = isFresh?.() === false;
    if (!send && !stale) return;
    const request = new AbortController();
    pending.current = request;
    setBusy(kind);
    setStartOutcome(null);
    setStopOutcome(null);
    const outcome: ActionOutcome = !send || stale ? {
      kind: "refused", code: "stale_status",
      message: "The controller’s state is being re-read. Try again in a moment.",
    } : await send(request.signal);
    if (!mounted.current || pending.current !== request) return;
    if (outcome.kind === "confirmed") {
      client.setQueryData<StatusReading>(["controller-status"], { status: outcome.status, receivedAt: performance.now() });
    }
    if (kind === "start") setStartOutcome(outcome);
    else setStopOutcome(outcome);
    // Read the controller back after every answer (never a second POST). The action stays busy
    // until that answer is in, so nothing is pressed against a state the page has not re-read.
    await Promise.resolve(refresh()).catch(() => undefined);
    if (!mounted.current || pending.current !== request) return outcome;
    const reading = client.getQueryData<StatusReading>(["controller-status"]);
    answeredState.current = visibleState(reading);
    if (outcome.kind === "confirmed" && reading) {
      const status = reading.status;
      const contradicted = kind === "start"
        ? status.state !== "manual" || status.active?.scenario !== outcome.status.active?.scenario
        : status.state === "manual" || status.state === "unknown";
      if (contradicted) {
        if (kind === "start") setStartOutcome(null);
        else setStopOutcome(null);
      }
    }
    if (!stale) cooldownUntil.current = performance.now() + 800;
    pending.current = null;
    setBusy(null);
    return outcome;
  }

  return {
    busy, startOutcome, stopOutcome,
    start: (request: ActivationRequest) => run("start", (signal) => postStart(request, signal)),
    stop: (scenario: ScenarioId) => run("stop", (signal) => postStop(scenario, signal)),
    // An unavailable button may request a fresh read, never a POST.
    refreshIfStale: (kind: "start" | "stop") => run(kind, null),
    clearStart: () => setStartOutcome(null),
  };
}
