// Playwright fixtures for mocked-mode tests.
// - `api`: serves the built bundles from disk by the real server's rules (store pages at /, files
//   under /ui/next/) and /ui/config from fixtures; every other
//   request must match a typed handler for its own origin. Unmocked same-origin requests, other origins and WebSockets
//   are aborted and recorded, and a test fails if any violation remains at teardown.
// - `capture(stateId, target?)`: screenshot for the review page, named by coverage state: the full
//   page, the first viewport ("viewport") or one element (a Locator).
import { existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import { basename, dirname, extname, join, normalize, sep } from "node:path";
import { test as base, expect, type BrowserContext, type Locator, type Route } from "@playwright/test";
import type { paths as ControllerPaths } from "../../src/api/generated/controller.ts";
import type { paths as GatewayPaths } from "../../src/api/generated/gateway.ts";
import { COVERAGE } from "../coverage.ts";
import { OPERATOR_UI_CONFIG, STORE_UI_CONFIG } from "../fixtures/gateway.ts";
import { PREVIEW_PORTS } from "./ports.ts";

export { expect };

export const STORE_ORIGIN = `http://127.0.0.1:${PREVIEW_PORTS.store}`;
export const OPERATOR_ORIGIN = `http://127.0.0.1:${PREVIEW_PORTS.operator}`;
export const STORE_URL = `${STORE_ORIGIN}/`;
export const OPERATOR_URL = `${OPERATOR_ORIGIN}/ui/next/`;
/** The test-only component gallery (e2e/gallery). It has no API: only its files and /ui/config. */
export const GALLERY_ORIGIN = `http://127.0.0.1:${PREVIEW_PORTS.gallery}`;
export const GALLERY_URL = `${GALLERY_ORIGIN}/ui/next/`;

/** States that exist only to check the harness itself (not storefront artboards). */
export const HARNESS_STATES = ["shell-store", "shell-operator", "gallery-components", "gallery-headers"] as const;

export const SCREENS_DIR = join(import.meta.dirname, "..", "..", "e2e-output", "screens");

type Method = "GET" | "HEAD" | "POST" | "PUT" | "DELETE";
type GatewayPath = Exclude<keyof GatewayPaths, "/health">;
type ControllerPath = Exclude<keyof ControllerPaths, "/health">;
export type MockRequest = {
  method: Method;
  path: string;
  query: URLSearchParams;
  body: unknown;
  headers: Record<string, string>;
};
export type MockReply = { status: number; body?: unknown; headers?: Record<string, string> } | "network-error";
type Handler = (request: MockRequest, params: Record<string, string>) => MockReply | Promise<MockReply>;

/**
 * Each origin's built files. They are served from disk with route.fulfill, by the same rules as
 * the real server (services/shared/ui.py register_bundle): files under assets/ and licenses/,
 * the entry page for any other path, 404 for an unknown file. Going through the vite preview
 * servers instead stalled for about 10 seconds now and then under parallel load.
 */
const WEB = join(import.meta.dirname, "..", "..");
const BUNDLE_DIRS: Record<string, string> = {
  [STORE_ORIGIN]: join(WEB, "dist", "store"),
  [OPERATOR_ORIGIN]: join(WEB, "dist", "operator"),
  [GALLERY_ORIGIN]: join(WEB, "e2e-output", "gallery-dist"),
};
const CONTENT_TYPES: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
  ".txt": "text/plain; charset=utf-8",
};

/** True only for a file whose name matches exactly: Windows ignores case, the real server does not. */
const isExactFile = (file: string) =>
  existsSync(file) && statSync(file).isFile() && readdirSync(dirname(file)).includes(basename(file));

/** The store's pages on the Gateway: the same allowlist as services/shared/ui.py STORE_PAGE. */
export const STORE_PAGE_PATTERN = "/(?:checkout|orders(?:/(?:OBS-[0-9]+-[0-9A-F]{4}|ATT-[0-9A-F]{4}-[0-9]{4}))?)?";
export const STORE_PAGE = new RegExp(`^(?:${STORE_PAGE_PATTERN})$`);

/** The store's entry page for an allowlisted page path, like the Gateway (P4-H). */
async function serveStorePage(route: Route) {
  const entry = join(BUNDLE_DIRS[STORE_ORIGIN] as string, "index.html");
  if (!existsSync(entry)) return route.fulfill({ status: 404, body: "bundle not built" });
  return route.fulfill({ path: entry, contentType: CONTENT_TYPES[".html"] ?? "text/html" });
}

async function serveBundle(route: Route, origin: string, path: string, search: string) {
  const root = BUNDLE_DIRS[origin] as string;
  // Like the real server: nothing at all is served while the bundle is not built.
  if (!existsSync(join(root, "index.html"))) return route.fulfill({ status: 404, body: "bundle not built" });
  const rest = path.slice("/ui/next/".length);
  const [folder] = rest.split("/");
  let file: string | null;
  if (folder === "assets" || folder === "licenses") {
    const candidate = normalize(join(root, rest));
    file = candidate.startsWith(root + sep) && isExactFile(candidate) ? candidate : null;
  } else if (rest.split("/").pop()?.includes(".")) {
    file = null; // a file name outside assets/ is never a page
  } else if (origin === STORE_ORIGIN) {
    // The store's pages moved to / (P4-H): an old /ui/next/<page> link redirects there.
    const page = `/${rest}`;
    if (!STORE_PAGE.test(page)) return route.fulfill({ status: 404, body: "not found" });
    return route.fulfill({ status: 308, headers: { location: page + search } });
  } else {
    file = join(root, "index.html"); // the operator's client-side routes (until P5)
  }
  if (!file || !existsSync(file)) return route.fulfill({ status: 404, body: "not found" });
  return route.fulfill({ path: file, contentType: CONTENT_TYPES[extname(file)] ?? "application/octet-stream" });
}

/** /api/attempts/{attempt_ref} -> ^/api/attempts/(?<attempt_ref>[^/]+)$ (paths hold only [a-z_/-]). */
function templateRegex(template: string) {
  const pattern = template
    .split("/")
    .map((part) => (part.startsWith("{") && part.endsWith("}") ? `(?<${part.slice(1, -1)}>[^/]+)` : part))
    .join("/");
  return new RegExp(`^${pattern}$`);
}

/** JSON bodies are parsed; anything else (beacons, forms, text) is passed on as text. */
function parseBody(text: string | null): unknown {
  if (!text) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export class MockApi {
  readonly calls: MockRequest[] = [];
  /** Per-request handling log, attached to failing tests. */
  readonly log: string[] = [];
  private violations: string[] = [];
  private handlers: { origin: string; method: Method; regex: RegExp; handler: Handler }[] = [];

  /** Answer a Gateway route, only on the storefront's origin (the Gateway serves the store). */
  onGateway(method: Method, path: GatewayPath, handler: Handler) {
    this.handlers.unshift({ origin: STORE_ORIGIN, method, regex: templateRegex(path), handler });
    return this;
  }

  /** Answer a controller route, only on the operator's origin. */
  onController(method: Method, path: ControllerPath, handler: Handler) {
    this.handlers.unshift({ origin: OPERATOR_ORIGIN, method, regex: templateRegex(path), handler });
    return this;
  }

  /** Returns and clears the recorded violations, for tests that expect one. */
  takeViolations() {
    const taken = this.violations;
    this.violations = [];
    return taken;
  }

  pendingViolations() {
    return [...this.violations];
  }

  /** Routes the whole browser context, so popups and new tabs are covered too. */
  async install(context: BrowserContext) {
    await context.routeWebSocket(/.*/, (socket) => {
      this.violations.push(`websocket ${socket.url()}`);
      return socket.close();
    });
    await context.route("**/*", async (route) => {
      const started = Date.now();
      const label = `${route.request().method()} ${route.request().url()}`;
      this.log.push(`start ${label}`);
      try {
        await this.handle(route);
        this.log.push(`${Date.now() - started}ms ${label}`);
      } catch (error) {
        this.violations.push(`mock handler error for ${label}: ${String(error)}`);
        this.log.push(`handler error after ${Date.now() - started}ms ${label}: ${String(error)}`);
        await route.abort("failed").catch(() => undefined);
      }
    });
  }

  private async handle(route: Route) {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method() as Method;
    if (url.origin !== STORE_ORIGIN && url.origin !== OPERATOR_ORIGIN && url.origin !== GALLERY_ORIGIN) {
      this.violations.push(`cross-origin ${method} ${url.origin}${url.pathname}`);
      return route.abort("blockedbyclient");
    }
    if (url.pathname === "/ui/config") {
      const config = url.origin === OPERATOR_ORIGIN ? OPERATOR_UI_CONFIG : STORE_UI_CONFIG;
      return route.fulfill({ status: 200, json: config });
    }
    if (url.pathname.startsWith("/ui/next/")) return serveBundle(route, url.origin, url.pathname, url.search);
    if (url.origin === STORE_ORIGIN && (method === "GET" || method === "HEAD") && STORE_PAGE.test(url.pathname)) {
      return serveStorePage(route);
    }
    if (url.pathname === "/favicon.ico") return route.fulfill({ status: 404, body: "no favicon" });

    const mock: MockRequest = {
      method,
      path: url.pathname,
      query: url.searchParams,
      body: parseBody(request.postData()),
      headers: request.headers(),
    };
    this.calls.push(mock);
    for (const { origin, method: m, regex, handler } of this.handlers) {
      const match = origin === url.origin && m === method ? regex.exec(url.pathname) : null;
      if (!match) continue;
      const reply = await handler(mock, { ...match.groups });
      if (reply === "network-error") return route.abort("failed");
      return route.fulfill({
        status: reply.status,
        headers: { "x-request-id": mock.headers["x-request-id"] ?? "mock", ...reply.headers },
        ...(reply.body === undefined ? {} : { json: reply.body }),
      });
    }
    this.violations.push(`unmocked ${method} ${url.pathname}`);
    return route.abort("failed");
  }
}

const KNOWN_STATES = new Set<string>([...COVERAGE.map((s) => s.id), ...HARNESS_STATES]);

type CaptureTarget = "page" | "viewport" | Locator;

export const test = base.extend<{ api: MockApi; capture: (stateId: string, target?: CaptureTarget) => Promise<void> }>({
  api: [
    async ({ page }, use, testInfo) => {
      const api = new MockApi();
      const diagnostics: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error" || message.type() === "warning") {
          diagnostics.push(`console ${message.type()}: ${message.text()}`);
        }
      });
      page.on("pageerror", (error) => diagnostics.push(`page error: ${error.message}`));
      page.on("requestfailed", (request) =>
        diagnostics.push(`request failed: ${request.method()} ${request.url()} (${request.failure()?.errorText})`),
      );
      await api.install(page.context());
      await use(api);
      const violations = api.pendingViolations();
      if (testInfo.status !== testInfo.expectedStatus || violations.length > 0) {
        const lines = [...diagnostics, "-- violations --", ...violations, "-- routed requests --", ...api.log];
        await testInfo.attach("browser-diagnostics", { body: lines.join("\n"), contentType: "text/plain" });
      }
      expect(violations, "unmocked, cross-origin or WebSocket requests").toEqual([]);
    },
    { auto: true },
  ],
  capture: async ({ page }, use, testInfo) => {
    await use(async (stateId, target = "page") => {
      expect(KNOWN_STATES.has(stateId), `unknown coverage state "${stateId}"`).toBe(true);
      const dir = join(SCREENS_DIR, testInfo.project.name);
      mkdirSync(dir, { recursive: true });
      await page.evaluate(() => document.fonts.ready);
      const path = join(dir, `${stateId}.png`);
      if (typeof target === "string") {
        await page.screenshot({ path, fullPage: target === "page", animations: "disabled" });
      } else {
        await target.screenshot({ path, animations: "disabled" });
      }
    });
  },
});
