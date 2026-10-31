// @vitest-environment node
// P5-F: what the contract fixture does with each browser request, decided in one pure function so a
// test can prove that a Node re-issue (route.fetch) is only ever made for the controller's exact
// origin, with no URL override and with redirects off. Everything else is aborted.
import { describe, expect, it, vi } from "vitest";
import ports from "../contracts/stack-ports.json" with { type: "json" };
import { REISSUE_HEADER } from "../e2e/support/genuine.ts";
import { reissueOptions, routeDecision } from "../e2e/support/route-policy.ts";

const origin = "http://127.0.0.1:18190";
const decide = (url: string, over: Partial<Parameters<typeof routeDecision>[0]> = {}) =>
  routeDecision({ url, method: "GET", redirectedFrom: false, controllerOrigin: origin, takeGenuine: () => false, ...over });

describe("routeDecision", () => {
  it.each([
    `${origin}/`,
    `${origin}/ui/next/`,
    `${origin}/faults/status?x=1#h`,
    `${origin}`,
    "http://127.0.0.1:018190/x", // the URL parser reads this as the same port: the same origin
    "http://0x7f.0.0.1:18190/x", // and this as 127.0.0.1: the same origin
    "http://user@127.0.0.1:18190/x",
  ])("re-issues %s from Node, because it is the controller's origin", (url) => {
    expect(decide(url)).toEqual({ action: "reissue" });
  });

  it.each([
    "http://127.0.0.1:18191/sentinel", // the harness's own control origin
    "http://localhost:18190/x",
    "http://[::1]:18190/x",
    "http://[::ffff:127.0.0.1]:18190/x",
    "https://127.0.0.1:18190/x",
    "ws://127.0.0.1:18190/x",
    "http://127.0.0.1:18190@evil.test/x", // the controller's origin written as user info
    "http://evil.test\\@127.0.0.1:18190/x",
    "http://evil.test/#http://127.0.0.1:18190/",
    "http://127.0.0.1:18190.evil.test/x",
    "http://example.com/",
    "data:text/html,hi",
    "blob:http://127.0.0.1:18190/0b7c",
    "file:///C:/x",
    "about:blank",
    "not a url",
    "",
    ...ports.live.flatMap((port) => [`http://127.0.0.1:${port}/x`, `http://localhost:${port}/x`]),
    ...Object.values(ports.preview).flatMap((port) => [`http://127.0.0.1:${port}/x`, `http://localhost:${port}/x`]),
  ])("aborts %j: it is not the controller's origin", (url) => {
    expect(decide(url)).toEqual({ action: "abort" });
  });

  it("aborts an off-origin request that arrived by a redirect, and never re-issues it", () => {
    expect(decide("http://127.0.0.1:18191/sentinel", { redirectedFrom: true })).toEqual({ action: "abort" });
    expect(decide(`${origin}/ui/next/`, { redirectedFrom: true })).toEqual({ action: "reissue" });
  });

  it("lets a request through as genuine only by an explicit pass, and asks about passes only for the controller's origin", () => {
    const take = vi.fn(() => true);
    expect(decide(`${origin}/faults/auto/stop`, { method: "POST", takeGenuine: take })).toEqual({ action: "continue" });
    expect(take).toHaveBeenCalledWith("POST", `${origin}/faults/auto/stop`);
    const never = vi.fn(() => true);
    for (const url of ["http://127.0.0.1:18191/sentinel", "http://127.0.0.1:8090/faults/status", "http://localhost:18190/x"]) {
      expect(decide(url, { takeGenuine: never })).toEqual({ action: "abort" });
    }
    expect(never).not.toHaveBeenCalled();
  });

  it("aborts a genuine request that carries a forged re-issue label, and says so", () => {
    // A page could label its own request as a Node re-issue, or a genuine one: evidence must not be forgeable.
    const take = vi.fn(() => true);
    for (const name of ["x-contract-reissued", "X-Contract-Reissued", "X-CONTRACT-REISSUED"]) {
      const decision = decide(`${origin}/faults/auto/stop`, { method: "POST", takeGenuine: take, headers: { [name]: "route.fetch" } });
      expect(decision.action).toBe("abort");
      expect(decision.reason).toMatch(/forged/i);
    }
    expect(take).toHaveBeenCalledTimes(3); // the pass matched, so the label is what is wrong, and it is recorded
  });

  it("does not mind the label on a request that would be re-issued anyway (the fixture replaces it)", () => {
    expect(decide(`${origin}/faults/status`, { headers: { "x-contract-reissued": "page" } })).toEqual({ action: "reissue" });
  });

  it("re-issues, rather than continues, when no pass matches", () => {
    expect(decide(`${origin}/faults/auto/stop`, { method: "POST", takeGenuine: () => false })).toEqual({ action: "reissue" });
  });
});

describe("reissueOptions (the only options route.fetch is ever given)", () => {
  it("has exactly headers, maxRedirects and timeout: no URL, method or body override", () => {
    const options = reissueOptions({ accept: "*/*", origin });
    expect(Object.keys(options).sort()).toEqual(["headers", "maxRedirects", "timeout"]);
  });

  it("never follows a redirect on its own", () => {
    expect(reissueOptions({}).maxRedirects).toBe(0);
  });

  it("is bounded in time", () => {
    const { timeout } = reissueOptions({});
    expect(timeout).toBeGreaterThan(0);
    expect(timeout).toBeLessThanOrEqual(60_000);
  });

  it("keeps the browser's headers and labels the request as a Node re-issue, whatever the page claimed", () => {
    const { headers } = reissueOptions({ accept: "*/*", "X-Contract-Reissued": "no" });
    expect(headers.accept).toBe("*/*");
    expect(headers[REISSUE_HEADER]).toBe("route.fetch");
    expect(Object.keys(headers).filter((name) => name.toLowerCase() === REISSUE_HEADER)).toEqual([REISSUE_HEADER]);
  });
});
