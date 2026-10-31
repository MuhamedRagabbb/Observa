// @vitest-environment node
// P5-F: an independent, browser-scoped network boundary. Playwright's route handler only sees the first
// request of a redirect chain: Chromium follows the later hops itself (Gate A trace: a 302 to the harness
// sentinel reached it). Chromium's own Network.setBlockedURLs sits below that, in the network stack, and
// applies to every hop. It is an ALLOW-list for the network schemes: the controller's origin first
// (block: false), then every other http, https, ws, wss and ftp URL is blocked. Order decides: the first
// matching pattern wins. Node's URLPattern is the same class Chromium uses for these strings.
import { describe, expect, it } from "vitest";
import ports from "../contracts/stack-ports.json" with { type: "json" };
import { installNetworkBoundary, isBlocked, networkBoundary } from "../e2e/support/network-boundary.ts";

const controller = "http://127.0.0.1:18190";
const boundary = networkBoundary(controller);

describe("networkBoundary", () => {
  it("allows the controller's origin first, then blocks every other network URL", () => {
    expect(boundary.urlPatterns).toEqual([
      { urlPattern: "http://127.0.0.1:18190/*", block: false },
      { urlPattern: "http://*:*/*", block: true },
      { urlPattern: "https://*:*/*", block: true },
      { urlPattern: "ws://*:*/*", block: true },
      { urlPattern: "wss://*:*/*", block: true },
      { urlPattern: "ftp://*:*/*", block: true },
    ]);
  });

  it.each([
    ["a live port", "http://127.0.0.1:8090"],
    ["a preview port", "http://127.0.0.1:18090"],
    ["localhost", "http://localhost:18190"],
    ["https", "https://127.0.0.1:18190"],
    ["no port", "http://127.0.0.1"],
    ["a path", "http://127.0.0.1:18190/x"],
    ["user info", "http://u@127.0.0.1:18190"],
    ["a privileged port", "http://127.0.0.1:80"],
    ["nothing", ""],
  ])("refuses %s for the controller's origin", (_name, origin) => {
    expect(() => networkBoundary(origin)).toThrow();
  });
});

describe("isBlocked (first matching pattern wins)", () => {
  it.each([
    "http://127.0.0.1:18190/ui/next/",
    "http://127.0.0.1:18190/faults/status?x=1#h",
    "http://127.0.0.1:18190",
    "http://user@127.0.0.1:18190/x",
  ])("lets the controller's own URL %s through", (url) => {
    expect(isBlocked(boundary, url)).toBe(false);
  });

  it.each([
    "http://127.0.0.1:18191/sentinel", // the harness's own control port
    "http://localhost:18190/x", // same port, another name: a different origin
    "http://[::1]:18190/x",
    "https://127.0.0.1:18190/x",
    "http://127.0.0.1/x",
    "http://user:pw@127.0.0.1:8090/x",
    "https://example.com/",
    "https://example.com:8443/a",
    "ws://127.0.0.1:18191/x",
    "wss://127.0.0.1:9090/x",
    "ftp://127.0.0.1/x",
  ])("blocks %s", (url) => {
    expect(isBlocked(boundary, url)).toBe(true);
  });

  it("blocks every live and preview port on every loopback spelling and scheme, redirect or not", () => {
    for (const port of [...ports.live, ...Object.values(ports.preview)]) {
      for (const host of ["127.0.0.1", "localhost", "[::1]", "0.0.0.0", "10.1.2.3"]) {
        for (const scheme of ["http", "https", "ws", "wss"]) {
          expect(isBlocked(boundary, `${scheme}://${host}:${port}/x`), `${scheme}://${host}:${port}`).toBe(true);
        }
      }
    }
  });

  it.each(["data:text/html,hi", "about:blank", "blob:http://127.0.0.1:18190/0b7c1a", "file:///C:/x"])(
    "leaves %s alone: it is not a network load",
    (url) => {
      expect(isBlocked(boundary, url)).toBe(false);
    },
  );

  it("treats an unparsable URL as blocked, never as allowed", () => {
    expect(isBlocked(boundary, "not a url")).toBe(true);
  });
});

describe("installNetworkBoundary (fail closed)", () => {
  const recorder = (fail?: string) => {
    const calls: { method: string; params: unknown }[] = [];
    return {
      calls,
      send: async (method: string, params?: unknown) => {
        calls.push({ method, params });
        if (method === fail) throw new Error(`${method} refused`);
        return {};
      },
    };
  };

  it("enables the network agent, then sets exactly the boundary's patterns", async () => {
    const session = recorder();
    await installNetworkBoundary(session, boundary);
    expect(session.calls).toEqual([
      { method: "Network.enable", params: undefined },
      { method: "Network.setBlockedURLs", params: { urlPatterns: boundary.urlPatterns } },
    ]);
  });

  it.each(["Network.enable", "Network.setBlockedURLs"])("fails when %s fails, so no test can run unprotected", async (method) => {
    await expect(installNetworkBoundary(recorder(method), boundary)).rejects.toThrow(/network boundary/i);
  });
});

describe("networkBoundary with the sentinel exemption (routed-layer self-test only)", () => {
  // The routed-layer redirect test must prove, on its own, that the route handler refuses the redirect. With
  // the full boundary an error could come from the boundary instead. So that ONE test exempts exactly the
  // harness sentinel: if the route layer ever delivered the redirect, Chromium would follow it and reach it.
  const control = "http://127.0.0.1:18191";
  const relaxed = networkBoundary(controller, { exemptSentinel: control });

  it("exempts exactly the sentinel path of the harness control origin, ahead of the blocks", () => {
    expect(relaxed.urlPatterns[0]).toEqual({ urlPattern: `${controller}/*`, block: false });
    expect(relaxed.urlPatterns[1]).toEqual({ urlPattern: `${control}/sentinel`, block: false });
    expect(relaxed.urlPatterns.slice(2).every((pattern) => pattern.block)).toBe(true);
    expect(relaxed.urlPatterns).toHaveLength(boundary.urlPatterns.length + 1);
  });

  it("lets the sentinel through and nothing else on the control origin", () => {
    expect(isBlocked(relaxed, `${control}/sentinel`)).toBe(false);
    for (const path of ["/", "/sentinelx", "/sentinel/deeper", "/__test__/reset", "/__test__/sentinel", "/orders/_control/runtime", "/x"]) {
      expect(isBlocked(relaxed, `${control}${path}`), path).toBe(true);
    }
  });

  it("still blocks every live and preview port and every other host", () => {
    for (const port of [...ports.live, ...Object.values(ports.preview)]) {
      expect(isBlocked(relaxed, `http://127.0.0.1:${port}/sentinel`), `${port}`).toBe(true);
    }
    expect(isBlocked(relaxed, "http://localhost:18191/sentinel")).toBe(true);
    expect(isBlocked(relaxed, "https://127.0.0.1:18191/sentinel")).toBe(true);
  });

  it.each([
    ["a live port", "http://127.0.0.1:8090"],
    ["a preview port", "http://127.0.0.1:18090"],
    ["the controller's own origin", "http://127.0.0.1:18190"],
    ["localhost", "http://localhost:18191"],
    ["https", "https://127.0.0.1:18191"],
    ["a path", "http://127.0.0.1:18191/sentinel"],
    ["a privileged port", "http://127.0.0.1:80"],
    ["nothing", ""],
  ])("refuses to exempt %s", (_name, origin) => {
    expect(() => networkBoundary(controller, { exemptSentinel: origin })).toThrow();
  });

  it("has no exemption unless asked for", () => {
    expect(networkBoundary(controller, {}).urlPatterns).toEqual(boundary.urlPatterns);
  });
});
