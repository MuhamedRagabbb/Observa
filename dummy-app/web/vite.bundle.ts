// One Vite build per bundle. Each bundle has its own root, so the store and the
// operator never share chunks.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin, type UserConfig } from "vite";
import { refuseLive } from "./preview-guard.ts";
import { BUNDLE_PATH } from "./src/shared/bundle-path.ts";

export const BUNDLES = ["store", "operator"] as const;
export type Bundle = (typeof BUNDLES)[number];

const here = (path: string) => fileURLToPath(new URL(path, import.meta.url));

// The OFL requires the licence to travel with the font files, so each bundle ships it.
const FONT_PACKAGES = {
  "instrument-serif": "@fontsource/instrument-serif",
  inter: "@fontsource-variable/inter",
  "jetbrains-mono": "@fontsource-variable/jetbrains-mono",
};

function fontLicences(): Plugin {
  return {
    name: "observa-font-licences",
    apply: "build",
    generateBundle() {
      for (const [family, pkg] of Object.entries(FONT_PACKAGES)) {
        this.emitFile({
          type: "asset",
          fileName: `licenses/${family}-OFL.txt`,
          source: readFileSync(here(`./node_modules/${pkg}/LICENSE`), "utf8"),
        });
      }
    },
  };
}

// `vite dev` (hot reload) proxies API calls to a preview project, by default the throwaway
// Compose project from docker-compose.preview.yml. The live stack's ports are refused: a dev
// preview against live would place real orders and inject real faults.
const DEV = {
  store: { port: 5180, env: "OBSERVA_GATEWAY_URL", target: "http://127.0.0.1:18000", paths: ["/api", "/ui/config"] },
  operator: {
    port: 5181,
    env: "OBSERVA_CONTROLLER_URL",
    target: "http://127.0.0.1:18090",
    paths: ["/faults", "/services/health", "/activity", "/ui/config"],
  },
} as const;

/**
 * The controller refuses a state-changing request whose Origin is not its own (controllers/
 * fault_injector/guard.py). Through this proxy the dev page's Origin (127.0.0.1:5181) reaches a
 * controller on another port, so it is rewritten to the controller's origin, but only when it is
 * exactly the dev page's own: any other page's Origin goes through unchanged and is refused.
 */
function originRewrite(name: Bundle, target: string) {
  if (name !== "operator") return {};
  const page = `http://127.0.0.1:${DEV.operator.port}`;
  const controller = new URL(target).origin;
  return {
    configure: (proxy: { on: (event: "proxyReq", hook: (proxyReq: { setHeader: (name: string, value: string) => void }, req: { headers: Record<string, string | string[] | undefined> }) => void) => void }) => {
      proxy.on("proxyReq", (proxyReq, req) => {
        if (req.headers.origin === page) proxyReq.setHeader("origin", controller);
      });
    },
  };
}

function devTarget(name: Bundle, env: Record<string, string | undefined>) {
  return refuseLive(DEV[name].env, env[DEV[name].env] ?? DEV[name].target);
}

export function bundleConfig(
  name: Bundle,
  env: Record<string, string | undefined> = process.env,
  command: "build" | "serve" = "build",
): UserConfig {
  const target = devTarget(name, env);
  return defineConfig({
    root: here(`./src/${name}`),
    // Built files always live under /ui/next/. Both apps' pages are at /, so their dev
    // servers serve from / for direct page links to work with hot reload.
    base: command === "serve" ? "/" : BUNDLE_PATH,
    publicDir: false,
    plugins: [react(), fontLicences()],
    build: {
      outDir: here(`./dist/${name}`),
      emptyOutDir: true,
      assetsInlineLimit: 0,
    },
    server: {
      host: "127.0.0.1",
      port: DEV[name].port,
      strictPort: true,
      proxy: Object.fromEntries(DEV[name].paths.map((path) => [path, { target, changeOrigin: true, ...originRewrite(name, target) }])),
    },
    // `vite preview` would otherwise inherit the dev proxy; the mocked browser tests need none.
    preview: { proxy: {} },
  });
}
