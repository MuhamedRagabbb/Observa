"""Operator controller against a throwaway Compose project: real health, recovery and disconnections.

Same guard as the fault scenarios (live + RUN_FAULT_TESTS + a throwaway COMPOSE_PROJECT_NAME). The
Docker commands name that project explicitly, so they can never stop a live container.
"""

import asyncio
import json
import os
import subprocess

import httpx
import pytest

from tests.integration.test_live import BASE, CONTROLLER, eventually
from tests.scenarios.test_live_scenarios import (  # noqa: F401  (live + throwaway-only guard)
    pytestmark,
    settled,
)

PROJECT = os.getenv("COMPOSE_PROJECT_NAME", "")
GROUND_TRUTH_ONLY = ('"root_cause"', '"root_cause_service"', '"expected_signals"')
ORDER = {"product_id": "sku-005", "quantity": 1, "customer_id": "operator-test"}


def compose(*args):
    assert PROJECT and PROJECT != "observa-dummy"
    subprocess.run(["docker", "compose", "-p", PROJECT, *args], check=True, capture_output=True)


@pytest.fixture
async def client():
    async with httpx.AsyncClient(timeout=20) as http:
        await http.post(CONTROLLER + "/faults/auto/stop")
        await settled(http)
        yield http
        await http.post(CONTROLLER + "/faults/auto/stop")


async def health(client):
    return {
        row["service"]: row["state"]
        for row in (await client.get(CONTROLLER + "/services/health")).json()["services"]
    }


async def all_healthy(client):
    states = await health(client)
    return states if len(states) == 4 and set(states.values()) == {"healthy"} else None


async def test_service_health_comes_from_real_probes_and_prometheus(client):
    states = await eventually(lambda: all_healthy(client), 60)
    assert set(states) == {"api-gateway", "orders-service", "inventory-service", "payments-service"}
    snapshot = (await client.get(CONTROLLER + "/services/health")).json()
    assert snapshot["metrics"] == "available"
    probed = {row["service"]: row["probe"] for row in snapshot["services"]}
    assert probed["orders-service"]["ok"] and probed["payments-service"]["ok"]
    assert (
        probed["api-gateway"] is None and probed["inventory-service"] is None
    )  # not reachable: metrics only
    assert all(row["error_ratio"] is not None for row in snapshot["services"])


async def test_f2_degrades_payments_then_the_controller_waits_for_recovery(client):
    await eventually(lambda: all_healthy(client), 60)
    response = await client.post(
        CONTROLLER + "/faults/activate",
        json={
            "scenario": "payments_errors",
            "duration_seconds": 120,
            "parameters": {"failure_rate": 1, "status_code": 503},
        },
    )
    assert response.status_code == 200
    try:

        async def payments_degraded():
            await asyncio.gather(*[client.post(BASE + "/api/orders", json=ORDER) for _ in range(5)])
            return (await health(client)).get("payments-service") == "degraded"

        await eventually(payments_degraded, 60)
        status = (await client.get(CONTROLLER + "/faults/status")).json()
        assert status["state"] == "manual" and status["active"]["expires_at"] > status["now"]
    finally:
        stopped = await client.post(CONTROLLER + "/faults/deactivate", json={"scenario": "payments_errors"})
    assert stopped.json()["state"] == "recovering"
    assert stopped.json()["controls"]["activate"] == {"enabled": False, "reason": "recovering"}
    early = await client.post(CONTROLLER + "/faults/activate", json={"scenario": "bad_deployment"})
    assert (early.status_code, early.json()["error_code"]) == (409, "recovering")  # enforced, not advisory

    await settled(client, 150)  # until a health check after the fault finds every service healthy
    entries = (await client.get(CONTROLLER + "/activity")).json()["entries"]
    events = [e["event"] for e in entries if e["event"] != "service_health_changed"]
    assert events[:6] == [
        "services_recovered", "control_rejected", "fault_stopped", "stop_requested", "fault_active", "fault_requested",
    ]  # fmt: skip
    assert any(e["event"] == "service_health_changed" and e["state"] == "degraded" for e in entries)
    bodies = json.dumps([stopped.json(), (await client.get(CONTROLLER + "/activity")).json()])
    assert not any(key in bodies for key in GROUND_TRUTH_ONLY)


async def test_without_prometheus_health_is_unknown_never_healthy(client):
    compose("stop", "prometheus")
    try:

        async def unknown():
            snapshot = (await client.get(CONTROLLER + "/services/health")).json()
            states = {row["state"] for row in snapshot["services"]}
            return snapshot["metrics"] == "unavailable" and states == {"unknown"}

        await eventually(unknown, 30)
    finally:
        compose("start", "prometheus")
    await eventually(lambda: all_healthy(client), 90)


async def test_an_unreachable_target_makes_the_stop_unconfirmed_until_it_is_back(client):
    response = await client.post(
        CONTROLLER + "/faults/activate", json={"scenario": "payments_errors", "duration_seconds": 120}
    )
    assert response.status_code == 200
    compose("stop", "payments")
    try:
        refused = await client.post(CONTROLLER + "/faults/deactivate", json={"scenario": "payments_errors"})
        assert (refused.status_code, refused.json()["error_code"]) == (503, "target_unavailable")
        status = (await client.get(CONTROLLER + "/faults/status")).json()
        assert status["state"] == "unknown" and status["active"]["status"] == "deactivation_unconfirmed"
        assert {name for name, c in status["controls"].items() if c["enabled"]} == {"deactivate"}
        blocked = await client.post(CONTROLLER + "/faults/activate", json={"scenario": "bad_deployment"})
        assert (blocked.status_code, blocked.json()["error_code"]) == (409, "status_unknown")

        async def payments_unavailable():
            return (await health(client)).get("payments-service") == "unavailable"

        await eventually(payments_unavailable, 30)
    finally:
        compose("start", "payments")

    async def stop_confirmed():
        retry = await client.post(CONTROLLER + "/faults/deactivate", json={"scenario": "payments_errors"})
        return retry.status_code == 200 and retry.json()["active"] is None

    await eventually(stop_confirmed, 60)
    entries = (await client.get(CONTROLLER + "/activity")).json()["entries"]
    assert "stop_unconfirmed" in [e["event"] for e in entries]


def reachable(service, host, port):
    """Whether ``service``'s container can open a TCP connection to host:port (2 s timeout)."""
    code = f"import socket; socket.create_connection(({host!r}, {port}), 2)"
    result = subprocess.run(
        ["docker", "compose", "-p", PROJECT, "exec", "-T", service, "python", "-c", code],
        capture_output=True,
    )
    return result.returncode == 0


def test_the_controller_and_the_storefront_side_cannot_reach_each_other():
    assert PROJECT and PROJECT != "observa-dummy"
    allowed = [("orders", 8001), ("payments", 8003), ("prometheus", 9090)]
    denied = [("gateway", 8000), ("inventory", 8002), ("postgres", 5432)]
    assert all(reachable("fault-injector", host, port) for host, port in allowed)
    assert not any(reachable("fault-injector", host, port) for host, port in denied)
    assert not reachable("gateway", "fault-injector", 8090)
    assert not reachable("inventory", "fault-injector", 8090)
