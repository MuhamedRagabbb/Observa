// @vitest-environment node
// P5-F Gate A: the contract fixture re-issues every allowed browser request from Node (route.fetch) so
// that it can refuse redirects. A re-issued request does not carry Chromium's own Origin and
// Sec-Fetch-* headers, so it is no evidence for the controller's origin guard. Evidence needs a
// GENUINE request, which the fixture lets through only by an explicit, single-use pass.
import { describe, expect, it } from "vitest";
import { GenuinePasses, REISSUE_HEADER, reissueHeaders } from "../e2e/support/genuine.ts";

const origin = "http://127.0.0.1:18190";

describe("GenuinePasses", () => {
  it("lets one exact request through once", () => {
    const passes = new GenuinePasses(origin);
    passes.allow("POST", `${origin}/faults/auto/stop`);
    expect(passes.pending()).toBe(1);
    expect(passes.take("POST", `${origin}/faults/auto/stop`)).toBe(true);
    expect(passes.take("POST", `${origin}/faults/auto/stop`)).toBe(false);
    expect(passes.pending()).toBe(0);
  });

  it("matches the method and the whole URL, nothing looser", () => {
    const passes = new GenuinePasses(origin);
    passes.allow("POST", `${origin}/faults/auto/stop`);
    for (const [method, url] of [
      ["GET", `${origin}/faults/auto/stop`],
      ["post", `${origin}/faults/auto/stop`],
      ["POST", `${origin}/faults/auto/stop?x=1`],
      ["POST", `${origin}/faults/auto/stop/`],
      ["POST", `${origin}/faults/auto/start`],
      ["POST", "http://127.0.0.1:8090/faults/auto/stop"],
      ["POST", "http://127.0.0.1:18191/faults/auto/stop"],
    ] as const) expect(passes.take(method, url), `${method} ${url}`).toBe(false);
    expect(passes.pending()).toBe(1);
  });

  it("only ever names the controller's origin", () => {
    const passes = new GenuinePasses(origin);
    for (const url of ["http://127.0.0.1:8090/faults/status", "http://127.0.0.1:18191/sentinel", "http://localhost:18190/x", "https://127.0.0.1:18190/x", "not a url"]) {
      expect(() => passes.allow("GET", url), url).toThrow();
    }
    expect(passes.pending()).toBe(0);
  });

  it("only passes GET and POST", () => {
    const passes = new GenuinePasses(origin);
    for (const method of ["PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]) expect(() => passes.allow(method, `${origin}/x`), method).toThrow();
  });

  it("counts a pass per request, so two passes allow two requests", () => {
    const passes = new GenuinePasses(origin);
    passes.allow("GET", `${origin}/a`);
    passes.allow("GET", `${origin}/a`);
    expect([passes.take("GET", `${origin}/a`), passes.take("GET", `${origin}/a`), passes.take("GET", `${origin}/a`)]).toEqual([true, true, false]);
  });
});

describe("reissueHeaders", () => {
  it("labels a re-issued request, whatever the page sent", () => {
    expect(reissueHeaders({ accept: "*/*" })).toEqual({ accept: "*/*", [REISSUE_HEADER]: "route.fetch" });
    expect(REISSUE_HEADER).toBe("x-contract-reissued");
  });

  it("cannot be spoofed in any letter case, so a page cannot pass a re-issued request off as genuine (a forged label on a GENUINE pass is caught separately, in route-policy.ts)", () => {
    const labelled = reissueHeaders({ "X-Contract-Reissued": "no", "X-CONTRACT-REISSUED": "no", accept: "*/*" });
    expect(Object.keys(labelled).filter((name) => name.toLowerCase() === REISSUE_HEADER)).toEqual([REISSUE_HEADER]);
    expect(labelled[REISSUE_HEADER]).toBe("route.fetch");
  });

  it("does not change the headers it was given", () => {
    const original = { accept: "*/*" };
    reissueHeaders(original);
    expect(original).toEqual({ accept: "*/*" });
  });
});
