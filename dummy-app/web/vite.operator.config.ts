import { defineConfig } from "vite";
import { bundleConfig } from "./vite.bundle.ts";

// `vite preview` serves the built files, so it keeps the build's base (/ui/next/).
export default defineConfig(({ command, isPreview }) => bundleConfig("operator", process.env, isPreview ? "build" : command));
