import json
from uuid import uuid4

import httpx


def charge_body(order_id):
    return {
        "order_id": order_id,
        "idempotency_key": order_id,
        "customer_id": "web-user-1",
        "amount_cents": 5400,
    }


async def test_lookup_void_and_late_charge_over_http(mesh):
    order_id = f"order-{uuid4()}"
    async with httpx.AsyncClient(transport=mesh["transport"], base_url="http://payments") as client:
        missing = await client.get(f"/payments/by-key/{order_id}")
        assert missing.status_code == 404 and missing.json()["error_code"] == "payment_not_found"

        voided = await client.post(f"/payments/{order_id}/void", json={"order_id": order_id})
        assert voided.status_code == 200 and voided.json()["status"] == "voided"
        found = await client.get(f"/payments/by-key/{order_id}")
        assert found.json() == {
            "payment_id": voided.json()["payment_id"],
            "order_id": order_id,
            "status": "voided",
        }

        late = await client.post("/payments/charge", json=charge_body(order_id))
        assert late.status_code == 409 and late.json()["error_code"] == "payment_voided"


async def test_void_after_charge_returns_the_charge(mesh):
    order_id = f"order-{uuid4()}"
    async with httpx.AsyncClient(transport=mesh["transport"], base_url="http://payments") as client:
        charged = await client.post("/payments/charge", json=charge_body(order_id))
        assert charged.status_code == 200
        voided = await client.post(f"/payments/{order_id}/void", json={"order_id": order_id})
        assert voided.json() == charged.json()
    events = [json.loads(line)["event"] for line in mesh["logs"].getvalue().splitlines()]
    assert "payment_void_skipped" in events and "payment_voided" not in events


async def test_void_rejects_invalid_order_ids(mesh):
    async with httpx.AsyncClient(transport=mesh["transport"], base_url="http://payments") as client:
        response = await client.post("/payments/key-1/void", json={"order_id": "not-an-order"})
        assert response.status_code == 422
