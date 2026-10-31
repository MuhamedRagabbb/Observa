/**
 * Where each bundle's files are served on its own origin: the Vite `base` (asset URLs stay
 * /ui/next/assets/... for both bundles).
 */
export const BUNDLE_PATH = "/ui/next/";

/**
 * Where each app's pages are on its origin: its router basename, and where the cross-app links
 * point. The store moved to / in P4-H, the operator in P5-G1.
 */
export const APP_PATHS = {
  store: "/",
  control: "/",
} as const satisfies Record<"store" | "control", string>;

/** A router basename from an app path: "/" stays "/", "/ui/next/" becomes "/ui/next". */
export const routerBasename = (path: string) => (path === "/" ? "/" : path.replace(/\/$/, ""));
