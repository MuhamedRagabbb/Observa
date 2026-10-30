// @vitest-environment node
// `vite dev` proxies API calls to a preview project. It must never default to, or accept, the
// live stack's ports: a dev preview against live would create real orders and faults.
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import type { ConfigEnv, UserConfigExport } from "vite";
import { describe, expect, it } from "vitest";
import { bundleConfig } from "../vite.bundle.ts";

type Proxy = Record<string, { target: string; changeOrigin?: boolean }>;
const proxy = (name: "store" | "operator", env: Record<string, string> = {}) =>
  bundleConfig(name, env).server?.proxy as Proxy;

describe("dev preview server", () => {
  it("serves on loopback ports of its own", () => {
    expect(bundleConfig("store", {}).server).toMatchObject({ host: "127.0.0.1", port: 5180, strictPort: true });
    expect(bundleConfig("operator", {}).server).toMatchObject({ host: "127.0.0.1", port: 5181, strictPort: true });
  });

  it("sends the store's API calls to the preview gateway by default", () => {
    const store = proxy("store");
    expect(Object.keys(store).sort()).toEqual(["/api", "/ui/config"]);
    for (const route of Object.values(store)) expect(route.target).toBe("http://127.0.0.1:18000");
  });

  it("sends the operator's calls to the preview controller and nothing to the gateway API", () => {
    const operator = proxy("operator");
    expect(Object.keys(operator).sort()).toEqual(["/activity", "/faults", "/services/health", "/ui/config"]);
    for (const route of Object.values(operator)) expect(route.target).toBe("http://127.0.0.1:18090");
  });

  it("follows OBSERVA_GATEWAY_URL and OBSERVA_CONTROLLER_URL", () => {
    expect(proxy("store", { OBSERVA_GATEWAY_URL: "http://127.0.0.1:28000" })["/api"]?.target).toBe("http://127.0.0.1:28000");
    expect(proxy("operator", { OBSERVA_CONTROLLER_URL: "http://127.0.0.1:28090" })["/faults"]?.target).toBe(
      "http://127.0.0.1:28090",
    );
  });

  it("refuses the live stack's ports", () => {
    expect(() => bundleConfig("store", { OBSERVA_GATEWAY_URL: "http://localhost:8000" })).toThrow(/live/);
    expect(() => bundleConfig("operator", { OBSERVA_CONTROLLER_URL: "http://127.0.0.1:8090/" })).toThrow(/live/);
  });
});

describe("preview and stack-mode guards", () => {
  it("serves `vite preview` (used by the mocked browser tests) without any proxy", () => {
    expect(bundleConfig("store", {}).preview?.proxy).toEqual({});
    expect(bundleConfig("operator", {}).preview?.proxy).toEqual({});
  });

  it("builds both bundles' files under /ui/next/; both dev servers serve their pages at /", async () => {
    expect(bundleConfig("store", {}).base).toBe("/ui/next/");
    expect(bundleConfig("operator", {}).base).toBe("/ui/next/");
    expect(bundleConfig("store", {}, "serve").base).toBe("/");
    expect(bundleConfig("operator", {}, "serve").base).toBe("/");
    // `vite preview` serves the built files, so it keeps the build's base.
    const { default: storeConfig } = await import("../vite.store.config.ts");
    const resolve = storeConfig as unknown as (env: { command: "serve" | "build"; mode: string; isPreview?: boolean }) => { base?: string };
    expect(resolve({ command: "serve", mode: "development" }).base).toBe("/");
    expect(resolve({ command: "serve", mode: "production", isPreview: true }).base).toBe("/ui/next/");
    expect(resolve({ command: "build", mode: "production" }).base).toBe("/ui/next/");
  });

  it.each<[string, ConfigEnv, string]>([
    ["dev", { command: "serve", mode: "development", isPreview: false }, "/"],
    ["preview", { command: "serve", mode: "production", isPreview: true }, "/ui/next/"],
    ["build", { command: "build", mode: "production", isPreview: false }, "/ui/next/"],
  ])("the exported operator config uses the correct base for %s", async (_name, env, base) => {
    const { default: operatorConfig } = await import("../vite.operator.config.ts");
    const resolve = async (exported: UserConfigExport) => typeof exported === "function" ? exported(env) : exported;
    const resolved = await resolve(operatorConfig);
    expect(resolved.base).toBe(base);
  });

  it("refuses to run stack-mode browser tests against the live gateway", () => {
    const run = spawnSync("npx", ["playwright", "test", "--list"], {
      cwd: join(import.meta.dirname, ".."),
      env: { ...process.env, E2E_STACK_URL: "http://127.0.0.1:8000/ui/next/" },
      encoding: "utf8",
      shell: true,
    });
    expect(run.status).not.toBe(0);
    expect(`${run.stdout}${run.stderr}`).toMatch(/E2E_STACK_URL=.*live stack port/);
  }, 60_000);
});

// P5-0: the controller accepts a state-changing request only from its own origin (guard.py). The
// dev proxy changes Host to the controller's, so the dev page's Origin (127.0.0.1:5181) would be
// refused. The proxy rewrites Origin to the controller's only when it is exactly the dev page's
// own origin; any other page's Origin is passed on unchanged and still refused.
describe("dev proxy and the controller's origin guard", () => {
  type Hook = (proxyReq: { setHeader: (name: string, value: string) => void }, req: { headers: Record<string, string | undefined> }) => void;
  function forwarded(name: "store" | "operator", origin: string | undefined, env: Record<string, string> = {}) {
    const route = (bundleConfig(name, env).server?.proxy as Record<string, { configure?: (proxy: unknown, options: unknown) => void }>)[
      name === "store" ? "/api" : "/faults"
    ];
    const hooks: Hook[] = [];
    route?.configure?.({ on: (event: string, hook: Hook) => event === "proxyReq" && hooks.push(hook) }, {});
    const headers: Record<string, string> = origin === undefined ? {} : { origin };
    for (const hook of hooks) hook({ setHeader: (key, value) => (headers[key.toLowerCase()] = value) }, { headers: origin === undefined ? {} : { origin } });
    return headers.origin;
  }

  it("rewrites the dev page's own Origin to the controller's", () => {
    expect(forwarded("operator", "http://127.0.0.1:5181")).toBe("http://127.0.0.1:18090");
    expect(forwarded("operator", "http://127.0.0.1:5181", { OBSERVA_CONTROLLER_URL: "http://127.0.0.1:28090/" })).toBe("http://127.0.0.1:28090");
  });

  it("passes any other Origin on unchanged, so the controller still refuses it", () => {
    for (const origin of ["https://evil.example", "null", "http://localhost:5181", "http://127.0.0.1:5180"]) {
      expect(forwarded("operator", origin), origin).toBe(origin);
    }
  });

  it("adds no Origin to a request that had none, and leaves the store's proxy alone", () => {
    expect(forwarded("operator", undefined)).toBeUndefined();
    expect(forwarded("store", "http://127.0.0.1:5180")).toBe("http://127.0.0.1:5180");
  });
});
