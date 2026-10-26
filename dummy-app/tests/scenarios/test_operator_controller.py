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
from tests.scenarios.test_live_scenarios import pytestmark  # noqa: F401  (live + throwaway-only guard)

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
    assert all(row["probe"]["ok"] and row["error_ratio"] is not None for row in snapshot["services"])


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

    async def idle():
        return (await client.get(CONTROLLER + "/faults/status")).json()["state"] == "idle"

    await eventually(idle, 150)  # the 5xx share is measured over the last minute
    entries = (await client.get(CONTROLLER + "/activity")).json()["entries"]
    events = [e["event"] for e in entries if e["event"] != "service_health_changed"]
    assert events[:5] == [
        "services_recovered",
        "fault_stopped",
        "stop_requested",
        "fault_active",
        "fault_requested",
    ]
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
