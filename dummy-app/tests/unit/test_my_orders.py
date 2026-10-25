"""My Orders and Order Details through the Gateway (in-memory mesh)."""

import json
import time
from datetime import datetime

from services.shared.models import RuntimeUpdate

CONTACT = {"name": "Demo name", "email": "demo@example.com"}


async def checkout(mesh, ref, items=(("sku-001", 2), ("sku-002", 1)), customer="web-user-1"):
    body = {
        "customer_id": customer,
        "contact": CONTACT,
        "items": [{"product_id": p, "quantity": q} for p, q in items],
    }
    return await mesh["client"].post("/api/checkout", json=body, headers={"Idempotency-Key": ref})


async def my_orders(mesh, customer="web-user-1", **params):
    return await mesh["client"].get("/api/orders", params={"customer_id": customer, **params})


async def test_my_orders_lists_the_customers_checkouts_newest_first(mesh):
    first = (await checkout(mesh, "ATT-0001-0001")).json()
    await checkout(mesh, "ATT-0002-0002", customer="web-user-2")
    second = (await checkout(mesh, "ATT-0003-0003", items=(("sku-002", 1),))).json()

    response = await my_orders(mesh)

    assert response.status_code == 200
    listed = response.json()
    assert [row["order_number"] for row in listed] == [second["order_number"], first["order_number"]]
    newest, oldest = listed
    assert {key: oldest[key] for key in first} == first  # the same view the checkout returned
    assert (newest["outcome"], newest["total_cents"]) == ("confirmed", second["total_cents"])
    assert datetime.fromisoformat(newest["created_at"]) >= datetime.fromisoformat(oldest["created_at"])


async def test_order_details_match_the_list_including_failed_multi_item_checkouts(mesh):
    await checkout(mesh, "ATT-0001-0001")
    await mesh["apps"]["payments"].state.runtime.update(
        RuntimeUpdate(expires_at=time.time() + 30, payment_failure_rate=1, payment_failure_status=503)
    )
    failed = await checkout(mesh, "ATT-0002-0002")
    assert failed.status_code == 503

    listed = (await my_orders(mesh)).json()
    details = [
        (
            await mesh["client"].get(
                f"/api/attempts/{row['attempt_ref']}", params={"customer_id": "web-user-1"}
            )
        ).json()
        for row in listed
    ]

    assert details == listed
    assert [(row["outcome"], row["retryable"]) for row in listed] == [
        ("payment_failed", True),
        ("confirmed", False),
    ]
    assert listed[0]["items"] == [
        {"product_id": "sku-001", "quantity": 2, "unit_price_cents": 1200},
        {"product_id": "sku-002", "quantity": 1, "unit_price_cents": 1200},
    ]
    assert listed[0]["total_cents"] == 3600


async def test_my_orders_is_empty_for_a_new_customer_and_rejects_bad_input(mesh):
    assert (await my_orders(mesh, customer="nobody-yet")).json() == []
    before = len(mesh["transport"].requests)
    malformed = await my_orders(mesh, customer="bad id&limit=1")
    assert (malformed.status_code, malformed.json()["error_code"]) == (422, "invalid_customer_id")
    forwarded = [host for _, host, _, _ in mesh["transport"].requests[before:] if host != "gateway"]
    assert forwarded == []  # refused at the Gateway, never forwarded
    for limit in (0, 51):
        assert (await my_orders(mesh, limit=limit)).status_code == 422
    missing = await mesh["client"].get("/api/orders")
    assert missing.status_code == 422


async def test_my_orders_honours_the_limit(mesh):
    for ref in ("ATT-0001-0001", "ATT-0002-0002", "ATT-0003-0003"):
        await checkout(mesh, ref)
    listed = (await my_orders(mesh, limit=2)).json()
    assert [row["attempt_ref"] for row in listed] == ["ATT-0003-0003", "ATT-0002-0002"]


async def test_the_legacy_order_routes_are_unchanged(mesh):
    created = await mesh["client"].post(
        "/api/orders", json={"product_id": "sku-001", "quantity": 1, "customer_id": "web-user-1"}
    )
    assert created.status_code == 201
    fetched = await mesh["client"].get(f"/api/orders/{created.json()['order_id']}")
    assert (fetched.status_code, fetched.json()["status"]) == (200, "completed")
    assert (await my_orders(mesh)).json() == []  # legacy single-item orders are not part of My Orders


async def test_my_orders_telemetry_carries_no_customer_or_order_identifiers(mesh):
    order = (await checkout(mesh, "ATT-0001-0001")).json()
    await my_orders(mesh)
    forbidden = ["web-user-1", "ATT-0001-0001", order["order_id"], order["order_number"]]
    routes = set()
    for reader in mesh["readers"].values():
        for resource in reader.get_metrics_data().resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    for point in metric.data.data_points:
                        text = str(dict(point.attributes))
                        assert not any(value in text for value in forbidden), (metric.name, text)
                        routes.add(point.attributes.get("http.route"))
    assert {"/api/orders", "/checkouts"} <= routes
    logs = [json.loads(line) for line in mesh["logs"].getvalue().splitlines()]
    listing = [log for log in logs if log.get("http.route") in ("/api/orders", "/checkouts")]
    assert listing and not any(value in json.dumps(log) for log in listing for value in forbidden)
