import { Button } from "../../shared/ui/Button.tsx";
import type { ScenarioId } from "../fault-contract.ts";
import { blockKeyRepeat } from "../manual/no-key-repeat.ts";
import type { FaultActionBusy } from "../manual/use-fault-actions.ts";
import type { Banner } from "./experiment.ts";

export type StopActions = {
  busy?: FaultActionBusy;
  onStop?: (scenario: ScenarioId, control: HTMLButtonElement) => void;
  onStopCycle?: (control: HTMLButtonElement) => void;
  onUnavailablePress?: (control: HTMLButtonElement) => void;
};

/** Both Stop surfaces use the controller's availability and the same press handling. */
export function ExperimentStopButton({ stop, label, compact = false, busy = null, onStop, onStopCycle, onUnavailablePress }: StopActions & {
  stop: NonNullable<Banner["stop"]>; label?: string; compact?: boolean;
}) {
  return <Button icon="stop" size="medium"
    variant={stop.kind === "stop-fault" || stop.kind === "stop-cycle" ? "danger" : "secondary"}
    aria-label={label}
    unavailable={!stop.enabled}
    loading={stop.kind === "stop-cycle" ? busy === "cycle-stop" : busy === "stop"}
    onKeyDown={blockKeyRepeat}
    onClickCapture={(event) => {
      if (!stop.enabled && busy === null) onUnavailablePress?.(event.currentTarget);
    }}
    onClick={(event) => {
      if (!stop.enabled) return;
      if (stop.kind === "stop-cycle") onStopCycle?.(event.currentTarget);
      else if (stop.scenario) onStop?.(stop.scenario, event.currentTarget);
    }}
  >{compact ? "Stop" : stop.label}</Button>;
}
