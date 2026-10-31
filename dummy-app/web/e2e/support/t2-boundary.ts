/** Default-deny controller route for pages, popups, frames and dedicated workers.
 * Shared/service workers and controller beacons are disabled, not intercepted;
 * controller keepalive mutations are refused before network. Install before pages.
 * Provenance is TEST ISOLATION, not security against external clients. An unrelated
 * identical method/path/body after the trusted click can take the one stamp.
 * Browser interception never removes races with external controller writers.
 * Callers must block Service Workers and retain their ownership/cleanup checks.
 */
import type { BrowserContext, Page, Request } from "@playwright/test";
import { MUTATIONS, MutationGate, type GateKind } from "./t2-evidence.ts";

type UiExpectation = {
  token: string; path: string; body: unknown;
  control: { role: "button"; name: string }; clickAt: number | null;
};
type ProvenanceWindow = Window & { __t2Action?: { armed: UiExpectation | null } };
const HEADER = "x-t2-provenance";

export async function installMutationBoundary(context: BrowserContext, options: {
  origin: string;
  gate: MutationGate;
  onViolation: (reason: string) => void;
  requireNoPages?: boolean;
  beforeForward?: (authorized: { request: Request; id: string }) => string | null;
  onAuthorized?: (authorized: { request: Request; id: string }) => void;
}): Promise<void> {
  if (options.requireNoPages && context.pages().length > 0) {
    throw new Error("T2 mutation boundary requires a context with no pages");
  }
  await context.addInitScript(({ origin, header }) => {
    const state = { armed: null as UiExpectation | null };
    (window as ProvenanceWindow).__t2Action = state;
    const now = performance.now.bind(performance);
    const equal = (a: unknown, b: unknown): boolean => {
      if (Object.is(a, b)) return true;
      if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b)) return false;
      const left = Object.keys(a).sort(), right = Object.keys(b).sort();
      return left.length === right.length && left.every((key, i) => key === right[i]
        && equal((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]));
    };
    document.addEventListener("click", (event) => {
      const armed = state.armed;
      if (!armed || !event.isTrusted) return;
      const control = event.target instanceof Element ? event.target.closest("button,[role]") : null;
      const role = control?.getAttribute("role") ?? (control?.tagName === "BUTTON" ? "button" : null);
      const name = (control?.getAttribute("aria-label") ?? control?.textContent ?? "").trim();
      if (role === armed.control.role && name === armed.control.name) armed.clickAt = now();
    }, { capture: true });
    const original = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
      let url: URL;
      try { url = new URL(input instanceof Request ? input.url : String(input), location.href); }
      catch { return original(input, init); }
      if (url.origin === origin && method !== "GET" && method !== "HEAD"
          && (init?.keepalive || (input instanceof Request && input.keepalive))) {
        throw new TypeError("T2 blocks controller keepalive mutations");
      }
      const armed = state.armed;
      if (!armed || armed.clickAt === null || now() - armed.clickAt > 5_000) return original(input, init);
      if (method !== "POST" || url.origin !== origin || url.pathname !== armed.path || url.search) return original(input, init);
      let text = init?.body;
      if (text === undefined && input instanceof Request) {
        // openapi-fetch supplies Request objects. Read a clone; preserve the original body and signal.
        try { text = input.body === null ? null : await input.clone().text(); }
        catch { return original(input, init); }
      }
      let body: unknown = null;
      if (text != null) {
        if (typeof text !== "string") return original(input, init);
        try { body = JSON.parse(text); }
        catch { return original(input, init); }
      }
      if (state.armed !== armed || armed.clickAt === null || now() - armed.clickAt > 5_000 || !equal(body, armed.body)) return original(input, init);
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      headers.set(header, armed.token);
      // Identical interleaving here is indistinguishable from the intended handler.
      state.armed = null; // One stamp per arm, even if native fetch subsequently rejects.
      // Chromium routes only the first URL in a redirect chain. Never replay a stamped POST.
      return original(input, { ...init, headers, redirect: "error" });
    };
    const sendBeacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = (url, data) => {
      try { if (new URL(String(url), location.href).origin === origin) return false; }
      catch { return sendBeacon(url, data); }
      return sendBeacon(url, data);
    };
    // Shared workers have no context-route interception or window fetch wrapper.
    Object.defineProperty(window, "SharedWorker", { value: undefined, writable: true, configurable: true });
    // Defense in depth; serviceWorkers:"block" is still required on the context.
    if (typeof ServiceWorkerContainer !== "undefined") {
      ServiceWorkerContainer.prototype.register = () => Promise.reject(new Error("T2 blocks Service Worker registration"));
    }
  }, { origin: options.origin, header: HEADER });

  let nextRequest = 0;
  await context.route((url) => url.origin === options.origin, async (route) => {
    const request = route.request();
    const headers = await request.allHeaders();
    const token = headers[HEADER] ?? null;
    for (const name of Object.keys(headers)) if (name.toLowerCase().startsWith("x-t2-")) delete headers[name];
    const method = request.method();
    if (method === "GET" || method === "HEAD") return route.fallback({ headers });
    let body: unknown;
    try { body = request.postDataJSON() ?? null; }
    catch { body = { invalidJson: Symbol("invalid JSON cannot match an armed JSON body") }; }
    const url = new URL(request.url());
    const id = `browser-${++nextRequest}`;
    const decision = options.gate.decide({ id, method, path: url.pathname + url.search, body, token, redirected: request.redirectedFrom() !== null });
    const refusal = !decision.allow ? decision.reason : options.beforeForward?.({ request, id });
    if (refusal) {
      options.onViolation(refusal);
      return route.abort("blockedbyclient");
    }
    options.onAuthorized?.({ request, id });
    return route.fallback({ headers });
  });
}

/** Arm only the target page: other pages/frames/popups start with no expectation. */
export async function armUiAction(page: Page, gate: MutationGate, options: {
  kind: GateKind;
  body: unknown;
  control: { role: "button"; name: string };
}): Promise<string> {
  const token = gate.arm(options.kind, options.body);
  await page.evaluate((armed) => {
    const state = (window as ProvenanceWindow).__t2Action;
    if (!state) throw new Error("T2 mutation boundary must be installed before navigation");
    state.armed = armed;
  }, { token, path: MUTATIONS[options.kind], body: options.body, control: options.control, clickAt: null });
  return token;
}
