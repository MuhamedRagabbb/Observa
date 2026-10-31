// @vitest-environment node
// P5-F T1: the isolation fixture re-issues every browser request from Node (route.fetch), so for those
// requests Node already holds the COMPLETE response before Chromium gets a byte of it. Ground truth is
// scanned there, fail-closed, and a forbidden or unreadable response is withheld from the browser. Only a
// request whose response was scanned successfully may skip the browser-side body read (which a page that
// cancels a poll can make impossible). Requests that did not take this path (genuine passes, test-level
// page.route fulfils) are never exempt.
import { describe, expect, it } from "vitest";
import { ScannedRequests, reissueVerdict, scanReissuedResponse } from "../e2e/support/reissue-scan.ts";

type Reply = { status?: number; type?: string | null; body?: string | Buffer | (() => Promise<Buffer>) };
function reply({ status = 200, type = "application/json", body = "{}" }: Reply = {}) {
  const calls = { body: 0 };
  return {
    calls,
    status: () => status,
    headers: (): Record<string, string> => (type === null ? {} : { "content-type": type }),
    body: async () => {
      calls.body += 1;
      return typeof body === "function" ? body() : Buffer.from(body);
    },
  };
}

describe("scanReissuedResponse", () => {
  it("accepts an ordinary controller answer", async () => {
    expect(await scanReissuedResponse(reply({ body: JSON.stringify({ state: "manual", controls: { activate: { enabled: false } } }) }))).toEqual({ kind: "clean" });
  });

  it.each(["root_cause", "root_cause_service", "expected_signals"])("reports the key %s at any depth, with its path", async (key) => {
    const scan = await scanReissuedResponse(reply({ body: JSON.stringify({ active: { nested: [{ [key]: "x" }] } }) }));
    expect(scan).toEqual({ kind: "leak", paths: [`active.nested[0].${key}`] });
  });

  it("reports a token inside a string value", async () => {
    expect(await scanReissuedResponse(reply({ body: JSON.stringify({ message: "see expected_signals" }) }))).toEqual({ kind: "leak", paths: ["message"] });
  });

  it.each([409, 422, 500, 503])("scans a JSON answer with status %i too", async (status) => {
    expect(await scanReissuedResponse(reply({ status, body: JSON.stringify({ root_cause: "x" }) }))).toEqual({ kind: "leak", paths: ["root_cause"] });
  });

  it("reads JSON written with escapes, because it decodes before it looks", async () => {
    expect(await scanReissuedResponse(reply({ body: '{"root_\\u0063ause_service":"x"}' }))).toEqual({ kind: "leak", paths: ["root_cause_service"] });
  });

  it("fails closed on a JSON answer that is not valid JSON, without echoing any of it", async () => {
    // The reason is fixed text: a parser message can quote the body, which may itself be the ground truth.
    expect(await scanReissuedResponse(reply({ body: '{"root_cause": nope' }))).toEqual({ kind: "unreadable", reason: "body is not valid JSON" });
  });

  it("fails closed when the body cannot be read", async () => {
    expect(await scanReissuedResponse(reply({ body: () => Promise.reject(new Error("socket closed: root_cause_service")) }))).toEqual({ kind: "unreadable", reason: "body could not be read" });
  });

  it.each([
    ["an invalid byte", Buffer.from([0x22, 0xff, 0x22])],
    ["a truncated multi-byte sequence", Buffer.from([0x22, 0xc3, 0x22])],
    ["a lone continuation byte", Buffer.from([0x7b, 0x22, 0x61, 0x22, 0x3a, 0x22, 0x80, 0x22, 0x7d])],
  ])("fails closed on bytes that are not UTF-8 (%s), instead of decoding them with replacement characters", async (_name, bytes) => {
    // Without fatal decoding these bytes become a replacement character and the body parses as valid JSON.
    const response = reply({ body: bytes });
    expect(await scanReissuedResponse(response)).toEqual({ kind: "unreadable", reason: "body is not valid JSON" });
  });

  it("withholds undecodable bytes and never exempts them", async () => {
    const verdict = reissueVerdict("u", await scanReissuedResponse(reply({ body: Buffer.from([0x22, 0xff, 0x22]) })));
    expect(verdict).toEqual({ deliver: false, violations: ["JSON inspection failed for u: body is not valid JSON"] });
    const ledger = new ScannedRequests();
    const request = {};
    ledger.record(request, verdict);
    expect(ledger.exempts(request)).toBe(false);
  });

  it("reads valid multi-byte UTF-8 normally", async () => {
    expect(await scanReissuedResponse(reply({ body: Buffer.from(JSON.stringify({ label: "é · ü ✓" }), "utf-8") }))).toEqual({ kind: "clean" });
  });

  it("treats an empty JSON body as nothing to scan", async () => {
    expect(await scanReissuedResponse(reply({ body: "" }))).toEqual({ kind: "clean" });
  });

  it.each([null, "", "text/html", "application/javascript", "text/plain", "font/woff2"])("does not read a body of type %s", async (type) => {
    const response = reply({ type, body: "root_cause everywhere" });
    expect(await scanReissuedResponse(response)).toEqual({ kind: "not-json" });
    expect(response.calls.body).toBe(0);
  });

  it.each([301, 302, 303, 307, 308])("does not read a %i redirect (the fixture refuses those separately)", async (status) => {
    const response = reply({ status, body: '{"root_cause":1}' });
    expect(await scanReissuedResponse(response)).toEqual({ kind: "not-json" });
    expect(response.calls.body).toBe(0);
  });
});

describe("reissueVerdict (what the fixture does with a scan)", () => {
  const url = "http://127.0.0.1:18190/faults/status";

  it("delivers a clean answer and a non-JSON one", () => {
    expect(reissueVerdict(url, { kind: "clean" })).toEqual({ deliver: true, violations: [] });
    expect(reissueVerdict(url, { kind: "not-json" })).toEqual({ deliver: true, violations: [] });
  });

  it("withholds ground truth and records every path, in the fixture's existing message format", () => {
    expect(reissueVerdict(url, { kind: "leak", paths: ["a.root_cause", "b[0]"] })).toEqual({
      deliver: false,
      violations: [`${url}: a.root_cause`, `${url}: b[0]`],
    });
  });

  it("withholds an unreadable answer and records it as the fixture always has", () => {
    expect(reissueVerdict(url, { kind: "unreadable", reason: "body is not valid JSON" })).toEqual({
      deliver: false,
      violations: [`JSON inspection failed for ${url}: body is not valid JSON`],
    });
  });
});

describe("ScannedRequests (who may skip the browser-side read)", () => {
  it("exempts nothing that was not recorded: genuine passes and test-level fulfils stay under the strict read", () => {
    const ledger = new ScannedRequests();
    expect(ledger.exempts({})).toBe(false);
  });

  it("exempts a request only after a delivered, successful scan, and only that very request", () => {
    const ledger = new ScannedRequests();
    const scanned = {};
    const other = {};
    ledger.record(scanned, reissueVerdict("u", { kind: "clean" }));
    expect(ledger.exempts(scanned)).toBe(true);
    expect(ledger.exempts(other)).toBe(false);
    const jsonless = {};
    ledger.record(jsonless, reissueVerdict("u", { kind: "not-json" }));
    expect(ledger.exempts(jsonless)).toBe(true);
  });

  it("never exempts a request whose scan found ground truth or could not be completed", () => {
    const ledger = new ScannedRequests();
    const leaked = {};
    const unreadable = {};
    ledger.record(leaked, reissueVerdict("u", { kind: "leak", paths: ["root_cause"] }));
    ledger.record(unreadable, reissueVerdict("u", { kind: "unreadable", reason: "x" }));
    expect(ledger.exempts(leaked)).toBe(false);
    expect(ledger.exempts(unreadable)).toBe(false);
  });
});
