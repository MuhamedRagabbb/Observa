import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.{ts,tsx}"],
    // Builds both bundles once, so the built-output tests check what actually ships.
    globalSetup: ["test/build-bundles.ts"],
  },
});
