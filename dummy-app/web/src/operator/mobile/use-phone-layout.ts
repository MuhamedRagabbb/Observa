import { useSyncExternalStore } from "react";

export const PHONE_QUERY = "(max-width: 47.99rem)";

function subscribe(onChange: () => void) {
  if (typeof window === "undefined" || !window.matchMedia) return () => {};
  const media = window.matchMedia(PHONE_QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

function getSnapshot() {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia(PHONE_QUERY).matches : false;
}

export function usePhoneLayout(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
