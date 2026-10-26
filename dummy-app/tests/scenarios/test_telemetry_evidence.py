"""P2-C: fault evidence across Prometheus, Loki, Jaeger and the ground-truth journal; health freshness,
outages and the recovery limit; telemetry privacy and cardinality; no root cause in operator APIs.

Throwaway Compose project only (same guard as the fault scenarios). Docker commands name that
project explicitly, so they can never touch a live container.
"""

import asyncio
import calendar
import json
import math
import time
from uuid import uuid4

import httpx
import pytest

from controllers.fault_injector.app import SCENARIO_NAMES, SIGNALS
from services.shared.telemetry import DURATION_BUCKETS_SECONDS
from tests.integration.test_live import BASE, CONTROLLER, JAEGER, LOKI, ORDERS, PROM, eventually
from tests.scenarios.test_live_scenarios import (  # noqa: F401  (live + throwaway-only guard)
    JOURNAL,
    pytestmark,
    settled,
)
from tests.scenarios.test_operator_controller import compose

ORDER = {"product_id": "sku-005", "quantity": 2, "customer_id": "evidence-user"}
CONTACT = {"name": "Evidence Check", "email": "evidence@example.com"}
GROUND_TRUTH_TEXT = [
    "root_cause",
    "expected_signals",
    "Orders DB connection-pool exhaustion",
    *{signal for signals in SIGNALS.values() for signal in signals},
]


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=32)) as http:
        await http.post(CONTROLLER + "/faults/auto/stop")
        await settled(http)
        yield http
        await http.post(CONTROLLER + "/faults/auto/stop")


async def prom(client, query):
    response = await client.get(PROM + "/api/v1/query", params={"query": query})
    response.raise_for_status()
    return response.json()["data"]["result"]


async def prom_value(client, query):
    result = await prom(client, query)
    values = [float(row["value"][1]) for row in result]
    return max(values) if values else None


async def p95(client, service, window="1m"):
    value = await prom_value(
        client,
        f'histogram_quantile(0.95, sum by (le) (rate(http_server_duration_seconds_bucket{{service_name="{service}"}}[{window}])))',
    )
    return None if value is None or math.isnan(value) else value


async def logs(client, query, since):
    params = {"query": query, "start": str(int(since * 1_000_000_000)), "limit": 500}
    streams = (await client.get(LOKI + "/loki/api/v1/query_range", params=params)).json()["data"]["result"]
    return [json.loads(line) for stream in streams for _, line in stream["values"]]


async def trace(client, trace_id):
    data = (await client.get(JAEGER + "/api/traces/" + trace_id)).json().get("data", [])
    return data[0] if data else None


async def operator_views(client, *responses):
    paths = ("/faults/status", "/activity", "/services/health", "/health")
    bodies = [(await client.get(CONTROLLER + path)).json() for path in paths]
    return json.dumps([*bodies, *(response.json() for response in responses)])


def assert_no_ground_truth(text):
    leaked = [value for value in GROUND_TRUTH_TEXT if value in text]
    assert not leaked, leaked


async def activate(client, scenario, parameters, duration=60):
    await settled(client)
    response = await client.post(
        CONTROLLER + "/faults/activate",
        json={"scenario": scenario, "duration_seconds": duration, "parameters": parameters},
    )
    assert response.status_code == 200, response.text
    assert_no_ground_truth(json.dumps(response.json()))
    return response.json()["active"]


async def deactivate(client, scenario):
    response = await client.post(CONTROLLER + "/faults/deactivate", json={"scenario": scenario})
    assert response.status_code == 200, response.text
    assert_no_ground_truth(json.dumps(response.json()))


def journal_record(incident_id):
    return next(
        r for r in map(json.loads, JOURNAL.read_text().splitlines()) if r["incident_id"] == incident_id
    )


def checkout_body(*quantities):
    items = [
        {"product_id": sku, "quantity": q} for sku, q in zip(("sku-001", "sku-003"), quantities, strict=True)
    ]
    return {"customer_id": "evidence-user", "contact": CONTACT, "items": items}


async def checkout(client, *quantities):
    ref = f"ATT-{uuid4().int % 0x10000:04X}-{uuid4().int % 10000:04d}"
    return await client.post(
        BASE + "/api/checkout", json=checkout_body(*quantities), headers={"Idempotency-Key": ref}
    )


async def test_request_latency_percentiles_are_real_after_the_bucket_fix(client):
    bounds = await prom(
        client, 'count by (le) (http_server_duration_seconds_bucket{service_name="api-gateway"})'
    )
    exported = sorted(float(row["metric"]["le"]) for row in bounds)
    assert exported == [*DURATION_BUCKETS_SECONDS, float("inf")]  # second-scale buckets reach Prometheus

    async def quiet_p95():
        value = await p95(client, "api-gateway")
        return value if value is not None and value < 1.0 else None

    # With millisecond buckets this read ~4.75 s whatever the traffic; a quiet minute must now be sub-second.
    assert 0 < await eventually(quiet_p95, 120) < 1.0


async def test_f1_evidence_in_every_backend_then_recovery(client):
    since = time.time()
    incident = await activate(client, "db_connection_exhaustion", {"hold_seconds": 10}, duration=40)
    try:
        responses = await asyncio.gather(*[client.post(BASE + "/api/orders", json=ORDER) for _ in range(16)])
        refused = [r for r in responses if r.status_code == 503]
        assert refused, [r.status_code for r in responses]
        assert_no_ground_truth(await operator_views(client))

        async def pool_timeouts():
            return await prom_value(
                client, 'sum(increase(db_pool_timeouts_total{service_name="orders-service"}[2m]))'
            )

        assert await eventually(pool_timeouts, 40) > 0
        found = await eventually(
            lambda: logs(client, '{service_name="orders-service"} |= "db_pool_timeout"', since), 40
        )
        assert any(r["event"] == "db_pool_timeout" and r["error.type"] == "PoolAcquireTimeout" for r in found)

        async def failed_acquire():
            spans = (await trace(client, refused[0].headers["x-trace-id"]) or {}).get("spans", [])
            return [
                s
                for s in spans
                if s["operationName"] == "db.acquire" and any(t["key"] == "error" for t in s["tags"])
            ]

        assert await eventually(failed_acquire, 40)
    finally:
        await deactivate(client, "db_connection_exhaustion")
    record = journal_record(incident["incident_id"])
    assert (record["root_cause"], record["root_cause_service"]) == (
        "Orders DB connection-pool exhaustion",
        "orders-service",
    )
    assert record["expected_signals"] == SIGNALS["db_connection_exhaustion"] and record["ended_at"]
    await settled(client)
    assert (await client.post(BASE + "/api/orders", json=ORDER)).status_code in (201, 402)


async def test_f2_evidence_errors_without_added_latency_and_visible_voids(client):
    since = time.time()
    voids_before = await prom_value(
        client,
        'sum(http_server_requests_total{service_name="payments-service",http_route="/payments/{idempotency_key}/void"})',
    )
    baseline = await p95(client, "payments-service") or 0.0
    incident = await activate(client, "payments_errors", {"failure_rate": 1, "status_code": 503})
    try:
        failed = await checkout(client, 1, 2)
        assert (failed.status_code, failed.json()["details"]["outcome"]) == (503, "payment_failed")
        await asyncio.gather(*[client.post(BASE + "/api/orders", json=ORDER) for _ in range(10)])
        assert_no_ground_truth(await operator_views(client))

        async def payments_failing():
            ratio = await prom_value(
                client,
                'sum(rate(http_server_requests_total{service_name="payments-service",http_status_code="503"}[1m])) / sum(rate(http_server_requests_total{service_name="payments-service"}[1m]))',
            )
            return ratio if ratio and ratio > 0.2 else None

        await eventually(payments_failing, 40)

        async def voids_visible():
            voids = await prom_value(
                client,
                'sum(http_server_requests_total{service_name="payments-service",http_route="/payments/{idempotency_key}/void"})',
            )
            return voids if voids and voids > (voids_before or 0) else None

        await eventually(voids_visible, 40)
        during = await p95(client, "payments-service")
        assert during is not None and during <= max(0.5, baseline * 3), (baseline, during)  # no added latency
        events = await eventually(
            lambda: logs(
                client, '{service_name="payments-service"} |~ "payment_failed|payment_voided"', since
            ),
            40,
        )
        assert {"payment_failed", "payment_voided"} <= {r["event"] for r in events}

        async def failed_charge_and_void():
            spans = (await trace(client, failed.headers["x-trace-id"]) or {}).get("spans", [])
            names = {s["operationName"] for s in spans}
            return (
                names if {"POST /payments/charge", "POST /payments/{idempotency_key}/void"} <= names else None
            )

        await eventually(failed_charge_and_void, 40)
    finally:
        await deactivate(client, "payments_errors")
    record = journal_record(incident["incident_id"])
    assert (
        record["root_cause_service"] == "payments-service"
        and record["expected_signals"] == SIGNALS["payments_errors"]
    )


async def test_f3_evidence_slow_failing_persistence_and_degraded_orders(client):
    since = time.time()
    incident = await activate(client, "bad_deployment", {"delay_seconds": 1.5})
    try:
        failures = await asyncio.gather(*[checkout(client, 1, 1) for _ in range(6)])  # even totals
        assert all(
            r.status_code == 500 and r.json()["error_code"] == "order_persistence_error" for r in failures
        )
        assert_no_ground_truth(await operator_views(client))

        async def orders_slow():
            value = await p95(client, "orders-service")
            return value if value and value >= 1.0 else None

        assert await eventually(orders_slow, 40) >= 1.0
        found = await eventually(
            lambda: logs(
                client, '{service_name="orders-service"} |~ "release_changed|order_persistence_failed"', since
            ),
            40,
        )
        assert "release_changed" in {r["event"] for r in found}
        assert any(
            r["event"] == "order_persistence_failed"
            and r["error.type"] == "OrderPersistenceCompatibilityError"
            for r in found
        )

        async def signature():
            spans = (await trace(client, failures[0].headers["x-trace-id"]) or {}).get("spans", [])
            slow = [
                s for s in spans if s["operationName"] == "order.persistence" and s["duration"] >= 1_500_000
            ]
            return slow if slow and "OrderPersistenceCompatibilityError" in json.dumps(slow) else None

        assert await eventually(signature, 40)

        async def orders_degraded():
            rows = (await client.get(CONTROLLER + "/services/health")).json()["services"]
            orders = next(r for r in rows if r["service"] == "orders-service")
            return orders if orders["state"] == "degraded" else None

        assert set((await eventually(orders_degraded, 40))["reasons"]) & {"high_latency", "high_error_ratio"}
    finally:
        await deactivate(client, "bad_deployment")
    assert journal_record(incident["incident_id"])["expected_signals"] == SIGNALS["bad_deployment"]
    assert (await client.get(ORDERS + "/health")).json()["service.version"] == "v1"


async def test_one_checkout_is_one_trace_across_four_services_with_matching_logs(client):
    placed = await checkout(client, 1, 2)
    assert placed.status_code in (201, 402), placed.text
    trace_id, since = placed.headers["x-trace-id"], time.time() - 60
    services = {"api-gateway", "orders-service", "inventory-service", "payments-service"}

    async def full_trace():
        data = await trace(client, trace_id)
        return data if data and {p["serviceName"] for p in data["processes"].values()} >= services else None

    assert await eventually(full_trace, 40)

    async def correlated_logs():
        found = await logs(client, f'{{service_name=~".+"}} |= "{trace_id}"', since)
        return found if {r["service.name"] for r in found} >= services else None

    for record in await eventually(correlated_logs, 40):
        assert record["trace_id"] == trace_id and record["request_id"] == placed.headers["x-request-id"]


async def test_telemetry_carries_no_contact_identifiers_or_unbounded_labels(client):
    placed = await checkout(client, 1, 1)
    forbidden_labels = {
        "customer_id",
        "order_id",
        "attempt_ref",
        "trace_id",
        "request_id",
        "scenario",
        "incident_id",
        "fault",
    }
    label_names = set((await client.get(PROM + "/api/v1/labels")).json()["data"])
    assert not label_names & forbidden_labels
    risky_markers = ("@", "ATT-", "order-", "inc-", "evidence-user", "load-user", *SCENARIO_NAMES)
    for label in label_names:
        values = (await client.get(f"{PROM}/api/v1/label/{label}/values")).json()["data"]
        risky = [v for v in values if any(marker in v for marker in risky_markers)]
        assert not risky, (label, risky[:5])
        if label not in ("__name__", "le", "quantile"):  # metric names and bucket bounds are fixed sets
            assert len(values) < 100, (label, len(values))
    names = (await client.get(PROM + "/api/v1/label/__name__/values")).json()["data"]
    application = [n for n in names if n.startswith(("http_", "dependency_", "db_", "orders_", "payments_"))]
    assert 0 < len(application) < 100
    loki_labels = set((await client.get(LOKI + "/loki/api/v1/labels")).json().get("data", []))
    assert not loki_labels & forbidden_labels
    found = await logs(
        client, '{service_name=~".+"} |~ "evidence@example.com|Evidence Check"', time.time() - 300
    )
    assert found == []
    spans = await eventually(lambda: trace(client, placed.headers["x-trace-id"]), 40)
    assert CONTACT["email"] not in json.dumps(spans) and CONTACT["name"] not in json.dumps(spans)


async def test_without_traffic_health_is_unknown_not_failed_and_recovery_waits_at_most_its_limit(client):
    compose("stop", "traffic-generator")
    try:

        async def idle_traffic_is_unknown():
            rows = {
                r["service"]: r
                for r in (await client.get(CONTROLLER + "/services/health")).json()["services"]
            }
            unprobed = [rows["api-gateway"], rows["inventory-service"]]
            if all(r["state"] == "unknown" and r["reasons"] == ["stale_metrics"] for r in unprobed):
                return rows
            return None

        rows = await eventually(idle_traffic_is_unknown, 90)
        snapshot = (await client.get(CONTROLLER + "/services/health")).json()
        checked = calendar.timegm(time.strptime(snapshot["checked_at"][:19], "%Y-%m-%dT%H:%M:%S"))
        assert time.time() - checked < 20  # the snapshot itself is fresh (checked every 5 s)
        assert (
            rows["orders-service"]["state"] == rows["payments-service"]["state"] == "healthy"
        )  # probes still pass
        assert not any(r["state"] == "unavailable" for r in rows.values())

        await activate(client, "payments_errors", {"failure_rate": 1}, duration=60)
        ended = time.monotonic()  # just before the stop: the fault ends during this call
        await deactivate(client, "payments_errors")
        blocked = await client.post(CONTROLLER + "/faults/activate", json={"scenario": "bad_deployment"})
        assert (blocked.status_code, blocked.json()["error_code"]) == (409, "recovering")

        async def unlocked():
            return (await client.get(CONTROLLER + "/faults/status")).json()["state"] == "idle"

        await eventually(unlocked, 200)
        waited = time.monotonic() - ended
        assert 175 <= waited <= 200, waited  # unknown health waits for the 180 s limit, no longer
    finally:
        compose("start", "traffic-generator")


async def test_a_collector_outage_makes_health_unknown_never_unavailable(client):
    compose("stop", "collector")
    try:

        async def scrape_down():
            snapshot = (await client.get(CONTROLLER + "/services/health")).json()
            reasons = {tuple(r["reasons"]) for r in snapshot["services"]}
            return (
                snapshot if snapshot["metrics"] == "unavailable" and reasons == {("scrape_down",)} else None
            )

        snapshot = await eventually(scrape_down, 60)
        assert {r["state"] for r in snapshot["services"]} == {"unknown"}
    finally:
        compose("start", "collector")

    async def healthy_again():
        rows = (await client.get(CONTROLLER + "/services/health")).json()["services"]
        return all(r["state"] == "healthy" for r in rows) or None

    await eventually(healthy_again, 120)
