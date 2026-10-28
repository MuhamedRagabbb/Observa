// P5-A: the Operator Dashboard's header and experiment banner, against the production build with
// typed controller mocks (e2e/fixtures/controller.ts). Every state is captured at 1440, 390 and 375
// beside its artboard (npm run e2e:review). The page only reads: any request but the mocked GETs
// (a POST to start or stop a fault, for example) is a harness violation and fails the test.
import type { Page } from "@playwright/test";
import { activity, activityEntry, controllerStatus, healthSnapshot, type ActivityEntry, type ControllerState, type HealthSnapshot } from "./fixtures/controller.ts";
import { expect, test, type MockApi, type MockReply } from "./support/harness.ts";
import { banner, header, noSideScroll, open, serveStatus, stepUntil, title } from "./support/operator-page.ts";

const STATES: [ControllerState, string, string, RegExp][] = [
  ["idle", "operator-idle", "Idle", /Experiment\s*Idle\s*No fault is running\.\s*No active timer/],
  ["manual", "operator-manual-active", "Payment Errors", /Manual fault · F2\s*Payment Errors\s*Started 13:58:42 · 90% of payment requests fail with HTTP 503\.\s*0[0-3]:\d\d\s*Remaining of 5:00/],
  ["automatic", "operator-automatic-cycle", "Payment Errors", /Automatic cycle · Repeat 2 of 3\s*Payment Errors\s*Fault phase · shuffled · seed 7341\s*0[01]:\d\d\s*Left in this phase/],
  ["recovering", "operator-recovering", "Waiting for services", /Recovering\s*Waiting for services\s*Fault stopped at 13:59:51\. Controls unlock when health checks pass\.\s*No active timer/],
  ["unknown", "operator-status-unknown", "Unknown", /Fault status\s*Unknown\s*The stop request wasn’t confirmed\. The fault may still be running\.\s*Unavailable\s*Check status/],
];

test.describe("experiment banner", () => {
  for (const [state, stateId, heading, text] of STATES) {
    test(`${state}: the controller's state, from GET /faults/status only`, async ({ page, api, capture }) => {
      serveStatus(api, () => ({ status: 200, body: controllerStatus(state) }));
      await open(page, heading);
      await expect(banner(page)).toHaveText(text);
      await expect(header(page).getByText("Controller connected")).toBeAttached();
      expect(await noSideScroll(page)).toBe(true);
      await capture(stateId);
    });
  }

  test("disconnected: the last confirmed state, Retry connection, and back to live once it answers", async ({ page, api, capture }) => {
    let up = false;
    serveStatus(api, () => (up ? { status: 200, body: controllerStatus("idle") } : "network-error"));
    await open(page, "Unreachable");
    await expect(banner(page)).toContainText("Can’t reach the local controller. Live state is unknown; nothing has been confirmed yet.");
    await expect(header(page).getByText("Controller disconnected")).toBeAttached();
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-disconnected");
    up = true;
    const retry = banner(page).getByRole("button", { name: "Retry connection" });
    await retry.focus();
    await page.keyboard.press("Enter");
    await stepUntil(page, () => title(page).textContent(), "Idle");
    await expect(title(page)).toBeFocused();
    await expect(header(page).getByText("Controller connected")).toBeAttached();
  });

  test("a running fault the controller stops answering about is shown as last confirmed, never counted down", async ({ page, api }) => {
    let up = true;
    serveStatus(api, () => (up ? { status: 200, body: controllerStatus("manual") } : { status: 503, body: { error_code: "unavailable", message: "x", request_id: "ctl-1" } }));
    await open(page, "Payment Errors");
    up = false;
    await page.clock.runFor(2500);
    await stepUntil(page, () => title(page).textContent(), "Unreachable");
    await expect(banner(page)).toContainText("the last confirmed state was a running fault");
    await expect(banner(page)).not.toContainText(/\d\d:\d\d/);
  });

  test("Check status in the unknown state reads again with a GET, nothing else", async ({ page, api }) => {
    let state: ControllerState = "unknown";
    const reads = serveStatus(api, () => ({ status: 200, body: controllerStatus(state) }));
    await open(page, "Unknown");
    const before = reads.count;
    state = "recovering";
    await banner(page).getByRole("button", { name: "Check status" }).click();
    await stepUntil(page, () => title(page).textContent(), "Waiting for services");
    // The check itself read once; stepping the clock may have let a regular poll read too.
    expect(reads.count).toBeGreaterThanOrEqual(before + 1);
  });
});

test.describe("header", () => {
  test("wordmark, Operator, the connection and Storefront ↗; on phones the words stay for screen readers", async ({ page, api }, info) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    await expect(header(page).getByRole("img", { name: "OBSERVA" })).toBeVisible();
    await expect(header(page).getByText("Operator", { exact: true })).toBeVisible();
    const link = header(page).getByRole("link", { name: /Storefront/ });
    await expect(link).toHaveAttribute("href", "http://127.0.0.1:18000/");
    const connection = header(page).getByText("Controller connected");
    if (info.project.name === "desktop-1440") {
      await expect(connection).toBeVisible();
      await expect(link).toContainText("Storefront");
    } else {
      // The 390 artboard shows just the dot and the arrow: the words are visually hidden only.
      await expect(connection).toBeAttached();
      expect(await connection.evaluate((el) => el.getBoundingClientRect().width)).toBeLessThanOrEqual(1);
      await expect(link).toHaveAccessibleName(/Storefront/);
    }
  });

  test("reads the status every 2.5 s (pausing while hidden is checked in test/operator-status.test.tsx)", async ({ page, api }) => {
    const reads = serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    const frozen = reads.count;
    for (let read = 1; read <= 3; read++) {
      await page.clock.runFor(2500);
      await stepUntil(page, async () => reads.count - frozen >= read, true);
    }
  });
});

test.describe("accessibility", () => {
  test("keyboard: Tab reaches Storefront then the banner's action, each with a visible focus ring", async ({ page, api }) => {
    serveStatus(api, () => "network-error");
    await open(page, "Unreachable");
    const link = header(page).getByRole("link", { name: /Storefront/ });
    const retry = banner(page).getByRole("button", { name: "Retry connection" });
    await page.keyboard.press("Tab");
    await expect(link).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(retry).toBeFocused();
    for (const target of [link, retry]) {
      await target.focus();
      const ring = await target.evaluate((el) => {
        const style = getComputedStyle(el);
        return style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2;
      });
      expect(ring).toBe(true);
    }
  });

  test("on phones every control is at least 44 x 44 px, the arrow-only Storefront link included", async ({ page, api }, info) => {
    test.skip(info.project.name === "desktop-1440", "phone sizes only");
    serveStatus(api, () => "network-error");
    await open(page, "Unreachable");
    for (const control of [header(page).getByRole("link", { name: /Storefront/ }), banner(page).getByRole("button", { name: "Retry connection" })]) {
      const box = await control.boundingBox();
      expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
      expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
    }
  });

  test("at 200% zoom (720 CSS px wide) nothing scrolls sideways and nothing is cut off", async ({ page, api }, info) => {
    test.skip(info.project.name !== "desktop-1440", "the desktop page zoomed");
    serveStatus(api, () => ({ status: 200, body: controllerStatus("manual") }));
    await page.setViewportSize({ width: 720, height: 450 });
    await open(page, "Payment Errors");
    expect(await noSideScroll(page)).toBe(true);
    for (const text of ["Payment Errors", "Remaining of 5:00"]) {
      const part = banner(page).getByText(text, { exact: true });
      await part.scrollIntoViewIfNeeded();
      await expect(part).toBeInViewport({ ratio: 1 });
    }
  });
});

// ---- P5-D: service health, observability links, session activity, planned enhancements ----

const health = (page: Page) => page.getByRole("region", { name: "Service health" });
const healthTable = (page: Page) => page.getByRole("table", { name: "Service health" });
const tools = (page: Page) => page.getByRole("list", { name: "Observability tools" });
const sessionLog = (page: Page) => page.getByRole("region", { name: "Session activity" });
const healthyCount = (page: Page) => healthTable(page).getByText("Healthy", { exact: true }).count();

/** Registered after serveStatus, so it wins over the default healthy/empty answers. */
function serveHealth(api: MockApi, reply: () => MockReply) {
  api.onController("GET", "/services/health", reply);
}
function serveActivity(api: MockApi, entries: () => ActivityEntry[] | "network-error") {
  api.onController("GET", "/activity", () => {
    const answer = entries();
    return answer === "network-error" ? answer : { status: 200, body: activity(answer) };
  });
}
const snapshot = (body: HealthSnapshot): MockReply => ({ status: 200, body });

/** An entry `seconds` after the page opened (the first status answer is CONTROLLER_NOW). */
const afterOpen = (event: string, seconds: number, extras: Record<string, unknown> = {}) => activityEntry(event, 120 + seconds, extras);

test.describe("service health", () => {
  test("idle: four real services, healthy, each with its last check; no sample data shown", async ({ page, api }, info) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    await stepUntil(page, () => healthyCount(page), 4);
    for (const name of ["api-gateway", "orders-service", "inventory-service", "payments-service"]) {
      await expect(healthTable(page).getByRole("rowheader", { name, exact: true })).toBeVisible();
    }
    // Phones leave out Last check, as the 390 artboard does.
    await expect(healthTable(page).getByRole("columnheader")).toHaveText(info.project.name === "desktop-1440" ? ["Service", "Status", "Last check"] : ["Service", "Status"]);
    await expect(page.getByText(/sample data/i)).toHaveCount(0);
    await expect(page.getByText("storefront-web")).toHaveCount(0);
    expect(await noSideScroll(page)).toBe(true);
  });

  test("a degraded service says why, and metrics being down is explained", async ({ page, api, capture }) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    serveHealth(api, () =>
      snapshot(
        healthSnapshot(
          {
            "payments-service": { state: "degraded", reasons: ["high_error_ratio", "high_latency"] },
            "orders-service": { state: "unavailable", reasons: ["probe_failed"] },
            "inventory-service": { state: "unknown", reasons: ["stale_metrics"] },
          },
          "unavailable",
        ),
      ),
    );
    await open(page, "Idle");
    await stepUntil(page, () => healthTable(page).getByText("Degraded", { exact: true }).count(), 1);
    const row = (name: string) => healthTable(page).getByRole("row").filter({ has: page.getByRole("rowheader", { name, exact: true }) });
    await expect(row("payments-service")).toContainText("Degraded");
    await expect(row("payments-service")).toContainText("High error ratio · High latency");
    await expect(row("orders-service")).toContainText("Unavailable");
    await expect(row("orders-service")).toContainText("Health probe failed");
    await expect(row("inventory-service")).toContainText("No recent traffic");
    await expect(health(page)).toContainText("Metrics are unavailable, so only services the controller can check directly are judged.");
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-health-degraded");
  });

  test("the health read failing makes every row Unknown —, and the answer coming back restores them", async ({ page, api }) => {
    let up = true;
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    serveHealth(api, () => (up ? snapshot(healthSnapshot()) : "network-error"));
    await open(page, "Idle");
    await stepUntil(page, () => healthyCount(page), 4);
    up = false;
    await page.clock.runFor(5000);
    await stepUntil(page, () => healthTable(page).getByText("Unknown", { exact: true }).count(), 4);
    await expect(healthTable(page).getByText("Controller not reachable")).toHaveCount(4);
    await expect(health(page)).toContainText("Can’t reach the controller, so service health is unknown.");
    up = true;
    await page.clock.runFor(5000);
    await stepUntil(page, () => healthyCount(page), 4);
  });

  test("controller disconnected: every row is Unknown, nothing is guessed", async ({ page, api, capture }) => {
    serveStatus(api, () => "network-error");
    await open(page, "Unreachable");
    await stepUntil(page, () => healthTable(page).getByText("Unknown", { exact: true }).count(), 4);
    await expect(healthTable(page).getByText("Healthy")).toHaveCount(0);
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-disconnected-health");
  });

  test("the Last check column shows on desktop and is hidden on phones, where the reasons stay", async ({ page, api }, info) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    serveHealth(api, () => snapshot(healthSnapshot({ "payments-service": { state: "degraded", reasons: ["high_latency"] } })));
    await open(page, "Idle");
    await stepUntil(page, () => healthTable(page).getByText("Degraded", { exact: true }).count(), 1);
    await expect(healthTable(page)).toContainText("High latency");
    const lastCheck = healthTable(page).getByRole("columnheader", { name: "Last check" });
    if (info.project.name === "desktop-1440") {
      await expect(lastCheck).toBeVisible();
      await expect(healthTable(page).locator("time")).toHaveCount(4);
    } else {
      await expect(lastCheck).toBeHidden();
    }
  });

  test("reads /services/health every 5 s", async ({ page, api }) => {
    let reads = 0;
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    serveHealth(api, () => {
      reads += 1;
      return snapshot(healthSnapshot());
    });
    await open(page, "Idle");
    await stepUntil(page, async () => reads >= 1, true);
    const first = reads;
    for (let next = 1; next <= 2; next++) {
      await page.clock.runFor(5000);
      await stepUntil(page, async () => reads >= first + next, true);
    }
  });
});

test.describe("observability links", () => {
  test("Grafana, Prometheus, Loki (Grafana Explore) and Jaeger, on the page's host with the configured ports", async ({ page, api }) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    const link = (name: RegExp) => tools(page).getByRole("link", { name });
    await expect(link(/Grafana.*Dashboards/)).toHaveAttribute("href", "http://127.0.0.1:13000/");
    await expect(link(/Prometheus.*Metrics/)).toHaveAttribute("href", "http://127.0.0.1:19090/");
    await expect(link(/Loki.*Logs/)).toHaveAttribute("href", "http://127.0.0.1:13000/explore");
    await expect(link(/Jaeger.*Traces/)).toHaveAttribute("href", "http://127.0.0.1:26686/");
    await expect(tools(page).getByRole("link")).toHaveCount(4);
  });

  test("keyboard reaches every link in order with a visible focus ring", async ({ page, api }) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    const links = tools(page).getByRole("link");
    await links.first().focus();
    for (let index = 0; index < 4; index++) {
      const current = links.nth(index);
      await expect(current).toBeFocused();
      const ring = await current.evaluate((el) => {
        const style = getComputedStyle(el);
        return style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2;
      });
      expect(ring).toBe(true);
      if (index < 3) await page.keyboard.press("Tab");
    }
  });

  test("on phones every link is at least 44 x 44 px, two to a row", async ({ page, api }, info) => {
    test.skip(info.project.name === "desktop-1440", "phone sizes only");
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    const boxes = await tools(page).getByRole("link").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON()));
    for (const box of boxes) {
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    expect(new Set(boxes.map((box) => Math.round(box.top))).size).toBe(2);
  });
});

test.describe("session activity", () => {
  test("empty at first: 'No activity in this session yet.'", async ({ page, api }) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    await expect(sessionLog(page)).toContainText("No activity in this session yet.");
    await expect(sessionLog(page).getByRole("list")).toHaveCount(0);
  });

  test("lists this session's entries newest first in English, never the ones from before the page opened", async ({ page, api, capture }) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("manual") }));
    const requested = { scenario: "payments_errors", duration_seconds: 300, parameters: { failure_rate: 0.9, status_code: 503 } };
    serveActivity(api, () => [
      activityEntry("fault_requested", 60, requested),
      afterOpen("fault_requested", 2, requested),
      afterOpen("fault_active", 4, { scenario: "payments_errors" }),
      afterOpen("service_health_changed", 9, { service: "payments-service", previous: "healthy", state: "degraded" }),
      afterOpen("some_future_event", 10),
    ]);
    await open(page, "Payment Errors");
    const log = sessionLog(page).getByRole("list", { name: "Session activity log" });
    await stepUntil(page, () => log.getByRole("listitem").count(), 4);
    await expect(log.getByRole("listitem")).toHaveText([
      /14:00:10\s*Some future event/,
      /14:00:09\s*payments-service · Healthy → Degraded/,
      /14:00:04\s*Controller confirmed fault is active/,
      /14:00:02\s*Fault requested · F2 Payment Errors · 90% · HTTP 503 · 300 s/,
    ]);
    await expect(sessionLog(page)).not.toContainText("No activity in this session yet.");
    expect(await noSideScroll(page)).toBe(true);
    await capture("operator-session-activity");
  });

  test("reads /activity?limit=200 every 5 s and shows a repeated entry once", async ({ page, api }) => {
    const reads: string[] = [];
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    api.onController("GET", "/activity", (request) => {
      reads.push(request.query.get("limit") ?? "");
      return { status: 200, body: activity([afterOpen("fault_active", 4, { scenario: "payments_errors" })]) };
    });
    await open(page, "Idle");
    await stepUntil(page, async () => reads.length >= 1, true);
    await page.clock.runFor(5000);
    await stepUntil(page, async () => reads.length >= 2, true);
    expect(new Set(reads)).toEqual(new Set(["200"]));
    await expect(sessionLog(page).getByRole("listitem")).toHaveCount(1);
  });

  test("an unreadable log says so and keeps what was listed", async ({ page, api }) => {
    let up = true;
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    serveActivity(api, () => (up ? [afterOpen("fault_active", 4, { scenario: "payments_errors" })] : "network-error"));
    await open(page, "Idle");
    await stepUntil(page, () => sessionLog(page).getByRole("listitem").count(), 1);
    up = false;
    await page.clock.runFor(5000);
    await stepUntil(page, () => sessionLog(page).getByText("Can’t read the controller’s activity log.", { exact: false }).count(), 1);
    await expect(sessionLog(page).getByRole("listitem")).toHaveCount(1);
  });
});

test.describe("planned enhancements", () => {
  test("a static, non-interactive reference list, labelled not available yet", async ({ page, api }) => {
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    await open(page, "Idle");
    const planned = page.getByRole("region", { name: "Planned enhancements" });
    await expect(planned).toContainText("Not available yet");
    await expect(planned).toContainText("Listed for reference only. None of these can be used from this dashboard.");
    await expect(planned.getByRole("listitem")).toHaveText([
      "Severity presets with editable parameters",
      "Affected-order rate for F3 Bad Deployment",
      "Per-scenario parameters inside the automatic cycle",
      "Traffic controls with safe operating limits",
    ]);
    await expect(planned.locator("button, a, input, select, textarea, [tabindex]")).toHaveCount(0);
  });
});

test.describe("layout", () => {
  test("at 200% zoom (720 CSS px wide) health, activity and the tool cards stay inside the page", async ({ page, api }, info) => {
    test.skip(info.project.name !== "desktop-1440", "the desktop page zoomed");
    serveStatus(api, () => ({ status: 200, body: controllerStatus("idle") }));
    serveHealth(api, () => snapshot(healthSnapshot({ "payments-service": { state: "degraded", reasons: ["high_error_ratio", "high_latency"] } })));
    serveActivity(api, () => [afterOpen("fault_requested", 2, { scenario: "payments_errors", duration_seconds: 300, parameters: { failure_rate: 0.9, status_code: 503 } })]);
    await page.setViewportSize({ width: 720, height: 450 });
    await open(page, "Idle");
    await stepUntil(page, () => sessionLog(page).getByRole("listitem").count(), 1);
    expect(await noSideScroll(page)).toBe(true);
    for (const region of [health(page), sessionLog(page), tools(page)]) {
      const box = await region.boundingBox();
      expect((box?.x ?? -1) + (box?.width ?? 0)).toBeLessThanOrEqual(720);
    }
  });
});
