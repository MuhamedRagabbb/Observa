// One Vite build per bundle. Each bundle has its own root, so the store and the
// operator never share chunks.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin, type UserConfig } from "vite";

export const BASE = "/ui/next/";
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

export function bundleConfig(name: Bundle): UserConfig {
  return defineConfig({
    root: here(`./src/${name}`),
    base: BASE,
    publicDir: false,
    plugins: [react(), fontLicences()],
    build: {
      outDir: here(`./dist/${name}`),
      emptyOutDir: true,
      assetsInlineLimit: 0,
    },
  });
}
