// @vitest-environment node
// P5-F: the harness controller never redirects, so a redirect answer is never delivered to the browser:
// Chromium would follow its next hop itself, outside Playwright's route handler. The fixture refuses it
// (aborts the request and records a violation) instead of fulfilling it, whatever its Location says.
import { describe, expect, it } from "vitest";
import { redirectViolation } from "../e2e/support/redirects.ts";

const url = "http://127.0.0.1:18190/faults/status";

describe("redirectViolation", () => {
  it.each([300, 301, 302, 303, 305, 307, 308, 399])("refuses a %i, even to the controller's own origin", (status) => {
    const same = redirectViolation(url, status, { location: "/ui/next/" });
    expect(same).toMatch(/^redirect refused: /);
    expect(same).toContain(String(status));
    expect(same).toContain(url);
    expect(redirectViolation(url, status, { location: "http://127.0.0.1:8090/faults/status" })).toContain("http://127.0.0.1:8090/faults/status");
  });

  it("refuses a redirect with no, an empty or a malformed Location too", () => {
    for (const headers of [{}, { location: "" }, { location: "http://[::bad" }, { Location: "http://127.0.0.1:18191/sentinel" }]) {
      expect(redirectViolation(url, 302, headers)).toMatch(/^redirect refused: /);
    }
  });

  it.each([200, 201, 202, 204, 206, 304, 400, 401, 403, 404, 409, 422, 500, 503])("delivers a %i", (status) => {
    expect(redirectViolation(url, status, { location: "http://127.0.0.1:18191/sentinel" })).toBeNull();
  });

  it("keeps the record bounded, so a hostile Location cannot flood the report", () => {
    const record = redirectViolation(url, 302, { location: `http://x.test/${"a".repeat(5000)}` });
    expect(record).not.toBeNull();
    expect(record!.length).toBeLessThan(600);
  });
});

describe("redirectViolation never carries a redirect nonce", () => {
  const nonce = "0123456789abcdef0123456789abcdef";

  it("redacts the nonce in the request URL and in the Location", () => {
    const record = redirectViolation(
      `http://127.0.0.1:18190/__harness__/redirect?to=x&nonce=${nonce}&status=302`,
      302,
      { location: `http://127.0.0.1:18191/sentinel?nonce=${nonce}` },
    );
    expect(record).not.toBeNull();
    expect(record).not.toContain(nonce);
    expect(record).toContain("nonce=[redacted]");
    expect(record).toContain("status=302"); // the rest of the URL stays readable
  });

  it("redacts any hex nonce parameter wherever it sits in the query", () => {
    const record = redirectViolation(`http://127.0.0.1:18190/r?nonce=${nonce}`, 307, { location: `/x?a=1&nonce=${nonce}` });
    expect(record).not.toMatch(/[0-9a-f]{32}/);
  });
});
