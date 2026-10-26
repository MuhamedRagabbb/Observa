"""Checkout traffic at concurrency against a throwaway Compose project (same guard as the fault scenarios).

Runs the generator's checkout flow in-process, then checks the databases: each completed order
has exactly one successful charge, nothing that ended unpaid was charged, and no order twice.
"""

import asyncio
import json
import os
import random
import time

import asyncpg
import httpx

from controllers.traffic_generator.main import run
from controllers.traffic_generator.mix import CONTACT
from services.shared.checkout import TERMINAL_STATES
from tests.integration.test_live import BASE, JAEGER, LOKI, PROM, eventually
from tests.scenarios.test_live_scenarios import pytestmark  # noqa: F401  (live + throwaway-only guard)

PORT = int(os.getenv("POSTGRES_PORT", "5432"))
TRAFFIC = {
    "TRAFFIC_FLOW": "checkout",
    "TRAFFIC_RPS": "20",
    "TRAFFIC_CONCURRENCY": "16",
    "TRAFFIC_SEED": "42",
    "GATEWAY_URL": BASE,
}


async def connect(service):
    password = os.getenv(f"{service.upper()}_DB_PASSWORD", f"local-{service}-only")
    return await asyncpg.connect(
        host="localhost", port=PORT, user=f"{service}_app", password=password, database=f"{service}_db"
    )


async def test_concurrent_checkout_traffic_never_charges_twice_or_charges_a_failed_order():
    """Pool pressure may refuse some requests (503) and, with the reconciler off, leave those attempts
    open; that may never produce a second charge or a charge on an order that ended unpaid, and no
    attempt may be left open without the client having seen a non-final answer."""
    prefix = random.randrange(0x10000)
    env = {**TRAFFIC, "TRAFFIC_RUN_PREFIX": str(prefix)}
    counts = await run(env, max_requests=400)
    assert "transport_error" not in counts and counts.get("201", 0) > 100, counts
    assert not {status for status in counts if status.startswith("5")} - {"503"}, counts

    orders, payments = await connect("orders"), await connect("payments")
    try:
        query = "SELECT order_id, state FROM checkout_attempts WHERE attempt_ref LIKE $1"
        pattern = f"ATT-{prefix:04X}-%"
        rows = await orders.fetch(query, pattern)
        for _ in range(20):  # let in-flight attempts finish
            if all(row["state"] in TERMINAL_STATES for row in rows):
                break
            await asyncio.sleep(0.5)
            rows = await orders.fetch(query, pattern)
        charged = await payments.fetch(
            "SELECT order_id, count(*) AS n FROM payments WHERE status='succeeded' AND order_id = ANY($1::text[]) "
            "GROUP BY order_id",
            [row["order_id"] for row in rows],
        )
    finally:
        await orders.close()
        await payments.close()
    charges = {row["order_id"]: row["n"] for row in charged}
    assert rows and all(n == 1 for n in charges.values())  # never twice
    for row in rows:
        if row["state"] == "completed":
            assert charges.get(row["order_id"]) == 1, row
        elif row["state"] in TERMINAL_STATES:
            assert row["order_id"] not in charges, row  # failed, declined or not placed: never charged
    still_open = [row for row in rows if row["state"] not in TERMINAL_STATES]
    # With the reconciler off, an attempt may stay open only after a visible non-final answer.
    unsettled = sum(counts.get(status, 0) for status in ("202", "503", "504", "transport_error"))
    assert len(still_open) <= unsettled, (len(still_open), counts)
    async with httpx.AsyncClient(timeout=10) as client:
        assert (
            await client.get(BASE + "/api/orders", params={"customer_id": "load-user-1"})
        ).status_code == 200

        async def pool_settled():
            # Leave no saturated pool reading behind: F1 judges saturation from this same metric.
            query = (
                'db_pool_active{service_name="orders-service"} / db_pool_max{service_name="orders-service"}'
            )
            series = (await client.get(PROM + "/api/v1/query", params={"query": query})).json()["data"][
                "result"
            ]
            return series and all(float(row["value"][1]) < 0.95 for row in series)

        await eventually(pool_settled, 30)


async def test_checkout_traffic_leaves_no_contact_data_or_attempt_references_in_telemetry():
    await run({**TRAFFIC, "TRAFFIC_RUN_PREFIX": str(random.randrange(0x10000))}, max_requests=60)
    contact = (CONTACT["email"], CONTACT["name"])
    async with httpx.AsyncClient(timeout=15) as client:

        async def checkout_traces():
            params = {
                "service": "api-gateway",
                "operation": "POST /api/checkout",
                "limit": 20,
                "lookback": "15m",
            }
            return (await client.get(JAEGER + "/api/traces", params=params)).json()["data"]

        traces = await eventually(checkout_traces)
        assert not any(value in json.dumps(traces) for value in contact)
        services = "api-gateway|orders-service|inventory-service|payments-service"
        for value in contact:
            query = f'{{service_name=~"{services}"}} |= "{value}"'
            start = str(int((time.time() - 900) * 1_000_000_000))
            found = await client.get(
                LOKI + "/loki/api/v1/query_range", params={"query": query, "start": start}
            )
            assert found.json()["data"]["result"] == [], value
        for label in (await client.get(PROM + "/api/v1/labels")).json()["data"]:
            values = (await client.get(f"{PROM}/api/v1/label/{label}/values")).json()["data"]
            assert not any("ATT-" in v or "@" in v for v in values), (
                label,
                [v for v in values if "ATT-" in v or "@" in v],
            )
