import asyncio
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests.integration.test_live import (
    BASE,
    CONTROLLER,
    INVENTORY,
    JAEGER,
    LOKI,
    ORDERS,
    PROM,
    eventually,
    successful_order,
)

LIVE_PROJECT = "observa-dummy"


def fault_target_refusal(env):
    """Why the fault scenarios must not run against this target, or None when they may.

    They saturate pools and fail payments, which leaves stuck rows in the legacy flow, so they
    run only against a throwaway Compose project (scripts/migrations/README.md).
    """
    if env.get("RUN_FAULT_TESTS") != "1":
        return "Fault scenarios are opt-in: set RUN_FAULT_TESTS=1 against a throwaway Compose project"
    if env.get("COMPOSE_PROJECT_NAME", "") in ("", LIVE_PROJECT):
        return f"Set COMPOSE_PROJECT_NAME to a throwaway project, never the live {LIVE_PROJECT!r}"
    return None


FAULT_REFUSAL = fault_target_refusal(os.environ)
pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_TESTS") != "1", reason="Set RUN_LIVE_TESTS=1 with Compose running"
    ),
    pytest.mark.skipif(FAULT_REFUSAL is not None, reason=FAULT_REFUSAL or ""),
]
ORDER = {"product_id": "sku-005", "quantity": 2, "customer_id": "scenario-user"}
# The controller writes here; a throwaway project redirects it with GROUND_TRUTH_DIR.
JOURNAL = Path(os.getenv("GROUND_TRUTH_DIR", "ground-truth")) / "incidents.jsonl"


@pytest.fixture
async def live():
    async with httpx.AsyncClient(timeout=20, limits=httpx.Limits(max_connections=32)) as client:
        result = await client.post(CONTROLLER + "/faults/auto/stop")
        assert result.status_code == 200
        yield client
        result = await client.post(CONTROLLER + "/faults/auto/stop")
        assert result.status_code == 200


async def settled(client, seconds=200):
    """Wait until the controller accepts new faults: it refuses them while services recover."""

    async def idle():
        return (await client.get(CONTROLLER + "/faults/status")).json()["state"] == "idle"

    await eventually(idle, seconds)


async def activate(client, scenario, parameters, duration=25):
    await settled(client)
    response = await client.post(
        CONTROLLER + "/faults/activate",
        json={
            "scenario": scenario,
            "duration_seconds": duration,
            "parameters": parameters,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["active"]


async def deactivate(client, scenario):
    response = await client.post(CONTROLLER + "/faults/deactivate", json={"scenario": scenario})
    assert response.status_code == 200, response.text


async def metrics(client, query):
    response = await client.get(PROM + "/api/v1/query", params={"query": query})
    response.raise_for_status()
    return response.json()["data"]["result"]


async def test_f1_real_pool_saturation_wait_timeouts_and_recovery(live):
    await successful_order(live)
    incident = await activate(live, "db_connection_exhaustion", {"hold_seconds": 10})
    start = time.perf_counter()
    requests = [asyncio.create_task(live.post(BASE + "/api/orders", json=ORDER)) for _ in range(16)]
    try:

        async def pool_saturated():
            series = await metrics(
                live,
                'db_pool_active{service_name="orders-service"} / db_pool_max{service_name="orders-service"}',
            )
            return series if any(float(row["value"][1]) >= 0.95 for row in series) else None

        await eventually(pool_saturated, 12)
        await deactivate(live, "db_connection_exhaustion")
        responses = await asyncio.gather(*requests)
    finally:
        await deactivate(live, "db_connection_exhaustion")
        await asyncio.gather(*requests, return_exceptions=True)
    assert any(response.status_code == 503 for response in responses)
    assert any(response.status_code == 201 for response in responses)
    assert time.perf_counter() - start >= 0.5
    await successful_order(live)
    series = await eventually(lambda: metrics(live, 'db_pool_timeouts_total{service_name="orders-service"}'))
    assert any(float(row["value"][1]) > 0 for row in series)
    journal = [json.loads(line) for line in JOURNAL.read_text().splitlines()]
    record = next(record for record in journal if record["incident_id"] == incident["incident_id"])
    assert record["ended_at"] and record["root_cause"] == "Orders DB connection-pool exhaustion"


async def test_f2_payment_errors_normal_latency_compensation_and_recovery(live):
    start = time.perf_counter()
    await successful_order(live)
    baseline = time.perf_counter() - start
    before_products = (await live.get(INVENTORY + "/inventory/products")).json()
    before = next(row["stock"] for row in before_products if row["product_id"] == "sku-005")
    await activate(live, "payments_errors", {"failure_rate": 1})
    start = time.perf_counter()
    responses = await asyncio.gather(*[live.post(BASE + "/api/orders", json=ORDER) for _ in range(8)])
    latency = (time.perf_counter() - start) / len(responses)
    assert all(response.status_code == 503 for response in responses)
    assert latency < max(0.5, baseline * 4)
    after_products = (await live.get(INVENTORY + "/inventory/products")).json()
    assert next(row["stock"] for row in after_products if row["product_id"] == "sku-005") == before
    trace_id = responses[0].headers["x-trace-id"]

    async def failed_payment_span():
        data = (await live.get(JAEGER + "/api/traces/" + trace_id)).json().get("data", [])
        if not data:
            return None
        return [
            span
            for span in data[0]["spans"]
            if span["operationName"] == "POST /payments/charge"
            and any(tag["key"] == "error" and tag["value"] for tag in span["tags"])
        ]

    await eventually(failed_payment_span)
    assert (await live.get(ORDERS + "/health")).status_code == 200
    await deactivate(live, "payments_errors")
    await successful_order(live)


async def test_f3_deployment_slow_span_new_error_rollback(live):
    health_before = (await live.get(ORDERS + "/health")).json()
    incident = await activate(live, "bad_deployment", {"delay_seconds": 1})
    health_during = (await live.get(ORDERS + "/health")).json()
    assert health_during["service.version"] == "v2-buggy"
    assert health_before["deployment.id"] != health_during["deployment.id"]
    start = time.perf_counter()
    response = await live.post(BASE + "/api/orders", json=ORDER)
    assert response.status_code == 500 and response.json()["error_code"] == "order_persistence_error"
    assert time.perf_counter() - start >= 1
    trace_id = response.headers["x-trace-id"]

    async def signature_ready():
        data = (await live.get(JAEGER + "/api/traces/" + trace_id)).json().get("data", [])
        if not data:
            return None
        spans = [span for span in data[0]["spans"] if span["operationName"] == "order.persistence"]
        return (
            spans
            if spans
            and spans[0]["duration"] >= 1000000
            and "OrderPersistenceCompatibilityError" in json.dumps(spans)
            else None
        )

    await eventually(signature_ready)
    await deactivate(live, "bad_deployment")
    assert (await live.get(ORDERS + "/health")).json()["service.version"] == "v1"
    await successful_order(live)
    # Controller labels/ground truth may not appear in any application log stream.
    result = await live.get(
        LOKI + "/loki/api/v1/query_range",
        params={
            "query": '{service_name=~"api-gateway|orders-service|payments-service|inventory-service"} |~ "payments_errors|bad_deployment|db_connection_exhaustion|incident_id"',
            "start": incident["started_at"],
        },
    )
    assert result.json()["data"]["result"] == []


def cart(ref, *quantities):
    items = [
        {"product_id": sku, "quantity": q} for sku, q in zip(("sku-001", "sku-003"), quantities, strict=True)
    ]
    body = {
        "customer_id": "scenario-user",
        "contact": {"name": "Scenario", "email": "s@example.com"},
        "items": items,
    }
    return {"json": body, "headers": {"Idempotency-Key": ref}}


async def test_f3_in_the_checkout_flow_fails_even_totals_with_the_same_signature(live):
    run = f"{uuid4().int % 0x10000:04X}"
    incident = await activate(live, "bad_deployment", {"delay_seconds": 1})
    try:
        start = time.perf_counter()
        failed = await live.post(
            BASE + "/api/checkout", **cart(f"ATT-{run}-0001", 1, 1)
        )  # odd lines, even total
        assert time.perf_counter() - start >= 1
        assert (failed.status_code, failed.json()["error_code"]) == (500, "order_persistence_error")
        assert failed.json()["details"]["state"] == "failed"
        placed = await live.post(BASE + "/api/checkout", **cart(f"ATT-{run}-0002", 1, 2))  # odd total
        assert placed.status_code in (201, 402), placed.text  # 402: the 2% baseline decline
        trace_id = failed.headers["x-trace-id"]

        async def signature_ready():
            data = (await live.get(JAEGER + "/api/traces/" + trace_id)).json().get("data", [])
            spans = [s for s in data[0]["spans"] if s["operationName"] == "order.persistence"] if data else []
            ready = spans and spans[0]["duration"] >= 1_000_000
            return spans if ready and "OrderPersistenceCompatibilityError" in json.dumps(spans) else None

        await eventually(signature_ready)
    finally:
        await deactivate(live, "bad_deployment")
    record = next(
        r
        for r in map(json.loads, JOURNAL.read_text().splitlines())
        if r["incident_id"] == incident["incident_id"]
    )
    assert (record["scenario"], record["root_cause_service"]) == ("bad_deployment", "orders-service")
    assert record["expected_signals"] == [
        "release_changed",
        "slow_order_persistence",
        "OrderPersistenceCompatibilityError",
    ]
    assert record["ended_at"]
    recovered = await live.post(BASE + "/api/checkout", **cart(f"ATT-{run}-0003", 1, 1))
    assert recovered.status_code in (201, 402), recovered.text  # v1 again: even totals complete


async def test_live_ttl_and_automatic_rotation(live):
    await activate(live, "payments_errors", {"failure_rate": 1}, duration=0.5)

    async def expired():
        state = (await live.get(CONTROLLER + "/faults/status")).json()
        return state["active"] is None

    await eventually(expired, 5)
    await successful_order(live)
    before = len(JOURNAL.read_text().splitlines())
    await settled(live)
    response = await live.post(
        CONTROLLER + "/faults/auto/start",
        json={
            "warmup_seconds": 0,
            "duration_seconds": 0.5,
            "recovery_seconds": 0.1,
            "seed": 42,
            "shuffle": False,
            "repeat": 1,
        },
    )
    assert response.status_code == 200
    assert (
        await live.post(CONTROLLER + "/faults/activate", json={"scenario": "payments_errors"})
    ).status_code == 409

    async def finished():
        state = (await live.get(CONTROLLER + "/faults/status")).json()
        return not state["auto_running"] and state["active"] is None

    await eventually(finished, 10)
    journal = [json.loads(line) for line in JOURNAL.read_text().splitlines()][before:]
    assert [record["scenario"] for record in journal] == [
        "db_connection_exhaustion",
        "payments_errors",
        "bad_deployment",
    ]
    assert all(record["ended_at"] for record in journal)
    await successful_order(live)
