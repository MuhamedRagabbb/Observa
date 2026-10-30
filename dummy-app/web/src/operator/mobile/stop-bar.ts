import type { Banner } from "../status/experiment.ts";

export function stopBar(banner: Banner) {
  const stop = banner.stop;
  if ((banner.kind !== "manual" && banner.kind !== "automatic")
    || !stop || (stop.kind !== "stop-fault" && stop.kind !== "stop-cycle")) return null;
  return {
    title: banner.kind === "automatic" && banner.title !== "Stopping the cycle" ? `Cycle · ${banner.title}` : banner.title,
    detail: banner.timer ? `${banner.timer.text} left` : banner.timerNote ?? "",
    stop,
    label: stop.kind === "stop-fault" ? `Stop ${banner.title}` : "Stop automatic cycle",
  };
}
