import asyncio
import json
import os
import subprocess
import time
from uuid import uuid4

import asyncpg
import httpx
import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_TESTS") != "1", reason="Set RUN_LIVE_TESTS=1 with Compose running"
    ),
]
BASE = os.getenv("TEST_GATEWAY_URL", "http://localhost:8000")
PROM = os.getenv("TEST_PROMETHEUS_URL", "http://localhost:9090")
JAEGER = os.getenv("TEST_JAEGER_URL", "http://localhost:16686")
LOKI = os.getenv("TEST_LOKI_URL", "http://localhost:3100")
# Direct service ports: override them to test a throwaway project published on other host ports.
ORDERS = os.getenv("TEST_ORDERS_URL", "http://localhost:8001")
INVENTORY = os.getenv("TEST_INVENTORY_URL", "http://localhost:8002")
PAYMENTS = os.getenv("TEST_PAYMENTS_URL", "http://localhost:8003")
CONTROLLER = os.getenv("TEST_CONTROLLER_URL", "http://localhost:8090")
ORDER = {"product_id": "sku-005", "quantity": 2, "customer_id": "integration-user"}


async def eventually(check, seconds=25):
    deadline = time.monotonic() + seconds
    while True:
        try:
            result = await check()
            if result:
                return result
        except (httpx.HTTPError, KeyError, IndexError):
            pass
        if time.monotonic() > deadline:
            raise AssertionError("Telemetry did not become queryable within the deadline")
        await asyncio.sleep(0.5)


async def successful_order(client):
    for _ in range(8):
        result = await client.post(BASE + "/api/orders", json=ORDER)
        if result.status_code != 402:
            assert result.status_code == 201, result.text
            return result
    raise AssertionError("No successful synthetic payment after eight attempts")


async def test_live_checkout_and_health():
    async with httpx.AsyncClient(timeout=15) as client:
        for service in (BASE, ORDERS, INVENTORY, PAYMENTS, CONTROLLER):
            assert (await client.get(f"{service}/health")).status_code == 200
        response = await successful_order(client)
        assert response.json()["status"] == "completed"
        assert (
            await client.get(BASE + "/api/orders/" + response.json()["order_id"])
        ).json() == response.json()


async def test_real_database_ownership_and_inventory_concurrency():
    password = os.getenv("ORDERS_DB_PASSWORD", "local-orders-only")
    connection = await asyncpg.connect(
        host="localhost",
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user="orders_app",
        password=password,
        database="orders_db",
    )
    assert await connection.fetchval("SELECT current_user") == "orders_app"
    await connection.close()
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await asyncpg.connect(
            host="localhost",
            port=int(os.getenv("POSTGRES_PORT", "5432")),
            user="orders_app",
            password=password,
            database="inventory_db",
        )
    async with httpx.AsyncClient(timeout=15) as client:
        products = (await client.get(INVENTORY + "/inventory/products")).json()
        before = next(p["stock"] for p in products if p["product_id"] == "sku-005")
        order_id = "order-" + str(uuid4())
        body = {"order_id": order_id, "product_id": "sku-005", "quantity": 2}
        reservations = await asyncio.gather(
            *[client.post(INVENTORY + "/inventory/reservations", json=body) for _ in range(12)]
        )
        try:
            assert all(r.status_code == 201 for r in reservations), [
                (r.status_code, r.text) for r in reservations
            ]
        finally:
            releases = await asyncio.gather(
                *[client.post(f"{INVENTORY}/inventory/reservations/{order_id}/release") for _ in range(12)]
            )
        assert all(r.status_code == 200 for r in releases)
        after = (await client.get(INVENTORY + "/inventory/products")).json()
        assert next(p["stock"] for p in after if p["product_id"] == "sku-005") == before
        late_order = "order-" + str(uuid4())
        await client.post(f"{INVENTORY}/inventory/reservations/{late_order}/release")
        assert (
            await client.post(INVENTORY + "/inventory/reservations", json={**body, "order_id": late_order})
        ).status_code == 409


async def test_real_payment_idempotency():
    async with httpx.AsyncClient(timeout=15) as client:
        order_id = "order-" + str(uuid4())
        body = {
            "order_id": order_id,
            "idempotency_key": order_id,
            "customer_id": "decline-integration",
            "amount_cents": 600,
        }
        responses = await asyncio.gather(
            *[client.post(PAYMENTS + "/payments/charge", json=body) for _ in range(8)]
        )
        assert all(r.status_code == 402 for r in responses), [(r.status_code, r.text) for r in responses]
        changed = await client.post(PAYMENTS + "/payments/charge", json={**body, "amount_cents": 700})
        assert changed.status_code == 409


async def test_three_signals_and_ground_truth_isolation():
    async with httpx.AsyncClient(timeout=15) as client:
        response = await successful_order(client)
        trace_id = response.headers["x-trace-id"]

        async def trace_ready():
            data = (await client.get(JAEGER + "/api/traces/" + trace_id)).json().get("data", [])
            if not data:
                return None
            services = {process["serviceName"] for process in data[0]["processes"].values()}
            return (
                data[0]
                if {"api-gateway", "orders-service", "inventory-service", "payments-service"} <= services
                else None
            )

        trace = await eventually(trace_ready)
        assert any(span["operationName"].startswith("db.") for span in trace["spans"])
        assert not any(
            value in json.dumps(trace)
            for value in ("db_connection_exhaustion", "payments_errors", "bad_deployment", "incident_id")
        )

        async def logs_ready():
            result = await client.get(
                LOKI + "/loki/api/v1/query_range",
                params={
                    "query": '{service_name=~"api-gateway|orders-service|inventory-service|payments-service"} |= "'
                    + trace_id
                    + '"',
                    "limit": 500,
                    "start": str(int((time.time() - 120) * 1_000_000_000)),
                },
            )
            streams = result.json()["data"]["result"]
            services = {stream["stream"]["service_name"] for stream in streams}
            return streams if len(services) == 4 else None

        streams = await eventually(logs_ready)
        for stream in streams:
            for _, line in stream["values"]:
                record = json.loads(line)
                assert {
                    "timestamp",
                    "level",
                    "service.name",
                    "event",
                    "request_id",
                    "trace_id",
                    "span_id",
                } <= record.keys()
                assert record["trace_id"] == trace_id
                assert record["request_id"] == response.headers["x-request-id"]
                assert "event" in record

        for service in ("api-gateway", "orders-service", "inventory-service", "payments-service"):

            async def metric_ready(service=service):
                response = await client.get(
                    PROM + "/api/v1/query",
                    params={
                        "query": f'http_server_requests_total{{service_name="{service}"}}',
                    },
                )
                return response.json()["data"]["result"]

            series = await eventually(metric_ready)
            for item in series:
                assert (
                    not {"request_id", "trace_id", "order_id", "customer_id", "scenario"}
                    & item["metric"].keys()
                )
        labels = (await client.get(LOKI + "/loki/api/v1/label/service_name/values")).json()["data"]
        assert "fault-controller" not in labels and "traffic-generator" not in labels


@pytest.mark.skipif(
    os.getenv("TEST_COLLECTOR_OUTAGE") != "1",
    reason="Opt in with TEST_COLLECTOR_OUTAGE=1; briefly stops Collector",
)
async def test_collector_downtime_is_fail_open():
    await asyncio.to_thread(
        subprocess.run, ["docker", "compose", "stop", "collector"], check=True, capture_output=True
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await successful_order(client)
            assert response.json()["status"] == "completed"
    finally:
        await asyncio.to_thread(
            subprocess.run, ["docker", "compose", "start", "collector"], check=True, capture_output=True
        )
