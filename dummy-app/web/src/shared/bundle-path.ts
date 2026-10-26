/**
 * Where this bundle is served on its own origin while the legacy UI keeps /. It is the
 * Vite `base` and the router basename.
 */
export const BUNDLE_PATH = "/ui/next/";

/**
 * Where each app is served on its origin; the cross-app links point here. Kept per app
 * because they move separately: P4 moves the store to /, P5 the operator.
 */
export const APP_PATHS = {
  store: "/ui/next/",
  control: "/ui/next/",
} as const satisfies Record<"store" | "control", string>;
