// @vitest-environment node
// P5-F: stack-mode operator tests may serve the page's own static files (the HTML and its hashed
// assets) from the local build, because the preview image predates P5. Only those files: every
// API path must reach the real controller, so nothing here may ever answer one.
import { describe, expect, it } from "vitest";
import { operatorBundleActive, operatorBundleFile } from "../e2e/support/bundle-routes.ts";

describe("operatorBundleFile", () => {
  it.each([
    ["GET", "/", "index.html"],
    ["HEAD", "/", "index.html"],
    ["GET", "/ui/next/assets/index-abc123.js", "assets/index-abc123.js"],
    ["GET", "/ui/next/assets/index-Zx_9-q.css", "assets/index-Zx_9-q.css"],
    ["GET", "/ui/next/assets/inter-latin-wght-normal-1a2b3c.woff2", "assets/inter-latin-wght-normal-1a2b3c.woff2"],
    ["GET", "/ui/next/licenses/OFL.txt", "licenses/OFL.txt"],
  ])("serves %s %s from the build as %s", (method, pathname, file) => {
    expect(operatorBundleFile(method, pathname)).toBe(file);
  });

  it.each([
    "/faults/status",
    "/faults/activate",
    "/faults/deactivate",
    "/faults/auto/start",
    "/faults/auto/stop",
    "/services/health",
    "/activity",
    "/ui/config",
    "/health",
    "/ui/next/",
    "/ui/next",
    "/ui/next/index.html",
    "/ui/next/other.html",
    "/ui/next/assets/",
    "/ui/next/assets/a/b.js",
    "/ui/next/assets/../../etc/passwd",
    "/ui/next/assets/..%2f..%2fsecret",
    "/ui/next/assets/%2e%2e",
    "/ui/next/assets/.hidden",
    "/ui/next/assets/a..b.js",
    "/ui/next/licenses/",
    "/ui/assets/control.js",
    "/ui/next/assets/status",
    "/ui/next/assets/config.json",
    "/ui/next/assets/index.js",
    "/ui/next/assets/index-abc123.json",
    "/ui/next/assets/index-abc123.html",
    "/ui/next/licenses/notes.js",
    "/ui/next/licenses/OFL",
  ])("never answers GET %s", (pathname) => {
    expect(operatorBundleFile("GET", pathname)).toBeNull();
  });

  it.each(["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "get"])("never answers a %s", (method) => {
    expect(operatorBundleFile(method, "/")).toBeNull();
    expect(operatorBundleFile(method, "/ui/next/assets/index-abc123.js")).toBeNull();
  });
});

describe("operatorBundleActive", () => {
  const controller = "http://127.0.0.1:18090";
  it("serves the local operator build only in local-bundle mode, for a test that allows the controller's origin", () => {
    expect(operatorBundleActive({ localBundle: true, controller, allowedOrigins: [controller] })).toBe(true);
    expect(operatorBundleActive({ localBundle: false, controller, allowedOrigins: [controller] })).toBe(false);
    expect(operatorBundleActive({ localBundle: true, controller, allowedOrigins: ["http://127.0.0.1:18000"] })).toBe(false);
    expect(operatorBundleActive({ localBundle: true, controller: "", allowedOrigins: [""] })).toBe(false);
  });
});
