// Test-only build of the component gallery (e2e/gallery). It never ships: the output goes to
// e2e-output/ (git-ignored), and the Dockerfile builds only the store and operator bundles.
import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { BUNDLE_PATH } from "./src/shared/bundle-path.ts";

const here = (path: string) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  root: here("./e2e/gallery"),
  base: BUNDLE_PATH,
  publicDir: false,
  plugins: [react()],
  build: { outDir: here("./e2e-output/gallery-dist"), emptyOutDir: true, assetsInlineLimit: 0 },
  preview: { proxy: {} },
});
