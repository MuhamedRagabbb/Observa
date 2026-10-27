/**
 * Where each bundle's files are served on its own origin: the Vite `base` (asset URLs stay
 * /ui/next/assets/... for both bundles). The operator's pages are here too until P5.
 */
export const BUNDLE_PATH = "/ui/next/";

/**
 * Where each app's pages are on its origin: its router basename, and where the cross-app links
 * point. Kept per app because they move separately: the store moved to / in P4-H, the operator
 * moves in P5.
 */
export const APP_PATHS = {
  store: "/",
  control: "/ui/next/",
} as const satisfies Record<"store" | "control", string>;

/** A router basename from an app path: "/" stays "/", "/ui/next/" becomes "/ui/next". */
export const routerBasename = (path: string) => (path === "/" ? "/" : path.replace(/\/$/, ""));
