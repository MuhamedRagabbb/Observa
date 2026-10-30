// Decide requests delivered to the route handler, by parsed-origin equality (including equivalent
// spellings and user info). Requests outside the controller's http origin are aborted. A Node reissue
// gets no URL, method or body override and never follows a redirect.
import { REISSUE_HEADER, reissueHeaders } from "./genuine.ts";
import { allowedOrigin } from "./stack-safety.ts";

export type RouteDecision = { action: "continue" | "reissue" | "abort"; reason?: string };

function isHttp(url: string): boolean {
  try {
    return new URL(url).protocol === "http:";
  } catch {
    return false;
  }
}

export function routeDecision({ url, method, redirectedFrom, controllerOrigin, takeGenuine, headers }: {
  url: string;
  method: string;
  /** The request is a redirect hop (Playwright knows it only when it routes the hop at all). */
  redirectedFrom: boolean;
  controllerOrigin: string;
  takeGenuine: (method: string, url: string) => boolean;
  headers?: Record<string, string>;
}): RouteDecision {
  // An off-origin URL is aborted whether or not it came by a redirect: a pass can never name one.
  // The scheme is checked too: a blob: URL carries the origin of the page that made it.
  if (!allowedOrigin(url, [controllerOrigin]) || !isHttp(url)) return { action: "abort" };
  // `redirectedFrom` changes nothing for an allowed URL: the controller's own origin is re-issued as usual.
  void redirectedFrom;
  if (!takeGenuine(method, url)) return { action: "reissue" };
  if (Object.keys(headers ?? {}).some((name) => name.toLowerCase() === REISSUE_HEADER)) {
    return { action: "abort", reason: "forged re-issue label on a genuine request" };
  }
  return { action: "continue" };
}

/** The only options route.fetch is ever given: the browser's headers plus a label, no redirects, a bound. */
export function reissueOptions(headers: Readonly<Record<string, string>>) {
  return { headers: reissueHeaders(headers), maxRedirects: 0, timeout: 30_000 } as const;
}
