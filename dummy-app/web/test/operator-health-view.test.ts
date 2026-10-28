// @vitest-environment node
// P5-D: what the Service health table says (src/operator/health/health-view.ts). Everything comes
// from GET /services/health; an unreachable controller turns every row to Unknown "—" rather than
// showing a stale state as current, and nothing is invented for a service the controller omits.
import { describe, expect, it } from "vitest";
import { healthSnapshot } from "../e2e/fixtures/controller.ts";
import { healthView, SERVICE_NAMES } from "../src/operator/health/health-view.ts";

const view = (snapshot: ReturnType<typeof healthSnapshot> | null, unreachable = false) => healthView({ snapshot, unreachable, timeZone: "UTC" });

describe("service health rows", () => {
  it("lists the controller's real service names, healthy, with the time of each check", () => {
    const { rows, notice } = view(healthSnapshot());
    expect(rows.map((row) => row.service)).toEqual(["api-gateway", "orders-service", "inventory-service", "payments-service"]);
    expect(rows[0]).toEqual({ service: "api-gateway", state: "healthy", label: "Healthy", shape: "filled", tone: "success", reasons: null, lastCheck: "13:59:58" });
    expect(notice).toBeNull();
  });

  it.each([
    ["degraded", "Degraded", "half", "ink"],
    ["unavailable", "Unavailable", "hollow", "fault"],
    ["unknown", "Unknown", "hollow", "muted"],
  ] as const)("%s reads %s with a %s %s marker", (state, label, shape, tone) => {
    const { rows } = view(healthSnapshot({ "orders-service": { state } }));
    expect(rows[1]).toMatchObject({ service: "orders-service", state, label, shape, tone });
  });

  it("says why a service is not healthy, in short words, every reason kept", () => {
    const { rows } = view(healthSnapshot({ "payments-service": { state: "degraded", reasons: ["high_error_ratio", "high_latency"] }, "orders-service": { state: "unavailable", reasons: ["probe_failed"] } }));
    expect(rows[3]?.reasons).toBe("High error ratio · High latency");
    expect(rows[1]?.reasons).toBe("Health probe failed");
  });

  it.each([
    ["stale_metrics", "No recent traffic"],
    ["no_metrics", "No metrics for this service"],
    ["metrics_unavailable", "Metrics unavailable"],
    ["something_new", "Something new"],
  ])("reason %s reads %s (an unknown reason is shown generically, never hidden)", (code, text) => {
    const { rows } = view(healthSnapshot({ "api-gateway": { state: "unknown", reasons: [code] } }));
    expect(rows[0]?.reasons).toBe(text);
  });

  it("shows — for a check time it cannot read", () => {
    const { rows } = view(healthSnapshot({ "api-gateway": { checked_at: "not a time" } }));
    expect(rows[0]?.lastCheck).toBe("—");
  });

  it("follows the snapshot's own services, adding none", () => {
    const snapshot = healthSnapshot();
    snapshot.services = snapshot.services.slice(0, 2);
    expect(view(snapshot).rows.map((row) => row.service)).toEqual(["api-gateway", "orders-service"]);
  });
});

describe("metrics notice", () => {
  it("metrics available: no notice", () => {
    expect(view(healthSnapshot({}, "available")).notice).toBeNull();
  });
  it("metrics unavailable: says health is judged from direct checks only", () => {
    expect(view(healthSnapshot({}, "unavailable")).notice).toBe("Metrics are unavailable, so only services the controller can check directly are judged.");
  });
  it("metrics unknown: says they have not been read yet", () => {
    expect(view(healthSnapshot({}, "unknown")).notice).toBe("Metrics have not been read yet.");
  });
});

describe("no usable answer", () => {
  it("before the first answer: every known service Unknown, no check time, no notice", () => {
    const { rows, notice } = view(null);
    expect(rows.map((row) => row.service)).toEqual([...SERVICE_NAMES]);
    for (const row of rows) expect(row).toMatchObject({ state: "unknown", label: "Unknown", lastCheck: "—", shape: "hollow", tone: "muted" });
    expect(notice).toBeNull();
  });

  it("controller unreachable: the last snapshot is not shown as current, every row is Unknown —", () => {
    const { rows, notice } = view(healthSnapshot({ "payments-service": { state: "degraded", reasons: ["high_latency"] } }), true);
    expect(rows.map((row) => [row.service, row.state, row.lastCheck, row.reasons])).toEqual(SERVICE_NAMES.map((name) => [name, "unknown", "—", "Controller not reachable"]));
    expect(notice).toBe("Can’t reach the controller, so service health is unknown.");
  });
});
