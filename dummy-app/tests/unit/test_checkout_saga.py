"""Multi-item checkout through the Gateway → Orders saga → Inventory/Payments (in-memory mesh)."""

import json
import re

CONTACT = {"name": "Demo name", "email": "demo@example.com"}
REF = "ATT-7F3A-0915"


def body(items=(("sku-001", 2), ("sku-002", 1)), customer="web-user-1"):
    return {
        "customer_id": customer,
        "contact": CONTACT,
        "items": [{"product_id": p, "quantity": q} for p, q in items],
    }


async def checkout(mesh, ref=REF, **kwargs):
    return await mesh["client"].post("/api/checkout", json=body(**kwargs), headers={"Idempotency-Key": ref})


def stock(mesh):
    return mesh["apps"]["inventory"].state.repository.stock


def payments(mesh):
    return mesh["apps"]["payments"].state.repository.rows


async def test_multi_item_checkout_completes_with_server_totals(mesh):
    response = await checkout(mesh)
    assert response.status_code == 201
    result = response.json()
    assert (result["state"], result["outcome"], result["retryable"]) == ("completed", "confirmed", False)
    assert result["attempt_ref"] == REF
    assert re.fullmatch(r"OBS-[0-9]{4,}-[0-9A-F]{4}", result["order_number"])
    assert result["items"] == [
        {"product_id": "sku-001", "quantity": 2, "unit_price_cents": 1200},
        {"product_id": "sku-002", "quantity": 1, "unit_price_cents": 1200},
    ]
    assert result["total_cents"] == 3600
    assert (stock(mesh)["sku-001"], stock(mesh)["sku-002"]) == (998, 999)
    [(charge, record, _)] = payments(mesh).values()
    assert (charge.amount_cents, record["status"]) == (3600, "succeeded")


async def test_contact_details_never_leave_the_gateway(mesh):
    await checkout(mesh)
    forwarded = [content for _, host, _, content in mesh["transport"].requests if host != "gateway"]
    assert forwarded and not any(b"demo@example.com" in c or b"Demo name" in c for c in forwarded)
    logs = mesh["logs"].getvalue()
    assert "demo@example.com" not in logs and "Demo name" not in logs
    assert REF not in logs  # attempt references are not logged either


async def test_replay_returns_the_same_order_without_charging_twice(mesh):
    first = (await checkout(mesh)).json()
    replay = await checkout(mesh, items=(("sku-002", 1), ("sku-001", 2)))
    assert replay.status_code == 201
    assert replay.json()["order_number"] == first["order_number"]
    assert len(payments(mesh)) == 1 and stock(mesh)["sku-001"] == 998
    conflict = await checkout(mesh, items=(("sku-001", 3),))
    assert conflict.status_code == 409 and conflict.json()["error_code"] == "idempotency_conflict"


async def test_checkout_requires_a_valid_idempotency_key(mesh):
    missing = await mesh["client"].post("/api/checkout", json=body())
    malformed = await mesh["client"].post("/api/checkout", json=body(), headers={"Idempotency-Key": "abc"})
    for response in (missing, malformed):
        assert response.status_code == 422
    assert not payments(mesh) and stock(mesh)["sku-001"] == 1000


async def test_status_lookup_is_read_only(mesh):
    lookup = await mesh["client"].get(f"/api/attempts/{REF}", params={"customer_id": "web-user-1"})
    assert lookup.status_code == 404 and lookup.json()["error_code"] == "attempt_not_found"
    # Not found must not fence: the same reference can still be placed afterwards.
    assert (await checkout(mesh)).status_code == 201
    found = await mesh["client"].get(f"/api/attempts/{REF}", params={"customer_id": "web-user-1"})
    assert found.status_code == 200 and found.json()["state"] == "completed"
    events = [json.loads(line)["event"] for line in mesh["logs"].getvalue().splitlines()]
    assert events.count("order_completed") == 1


async def payment_policy(mesh, status, rate=1.0):
    import time

    from services.shared.models import RuntimeUpdate

    await mesh["apps"]["payments"].state.runtime.update(
        RuntimeUpdate(expires_at=time.time() + 60, payment_failure_rate=rate, payment_failure_status=status)
    )


async def resolve(mesh, ref=REF):
    return await mesh["client"].post(f"/api/attempts/{ref}/resolve", params={"customer_id": "web-user-1"})


async def test_unavailable_item_is_not_placed_and_names_the_item(mesh):
    response = await checkout(mesh, items=(("sku-001", 1), ("sku-004", 2)))
    assert response.status_code == 409
    error = response.json()
    assert error["error_code"] == "insufficient_stock"
    assert error["details"]["outcome"] == "unavailable" and error["details"]["retryable"] is False
    assert error["details"]["unavailable_items"] == [
        {"product_id": "sku-004", "requested": 2, "available": 0}
    ]
    assert stock(mesh)["sku-001"] == 1000 and not payments(mesh)


async def test_definitive_402_decline_releases_stock(mesh):
    await payment_policy(mesh, 402)
    response = await checkout(mesh)
    assert response.status_code == 402
    assert response.json()["details"]["state"] == "payment_declined"
    assert response.json()["details"]["retryable"] is True
    assert stock(mesh)["sku-001"] == 1000


async def test_payment_503_is_settled_by_a_void_before_releasing(mesh):
    await payment_policy(mesh, 503)
    response = await checkout(mesh)
    assert response.status_code == 503
    details = response.json()["details"]
    assert (details["state"], details["outcome"], details["retryable"]) == (
        "payment_failed",
        "payment_failed",
        True,
    )
    [(_, record, _)] = payments(mesh).values()
    assert record["status"] == "voided"  # fenced: a late charge with this key can never succeed
    assert stock(mesh)["sku-001"] == 1000


async def test_charge_committed_but_response_lost_completes_and_never_releases(mesh):
    mesh["transport"].fail("POST", "payments", "/payments/charge", after_delivery=True, times=2)
    response = await checkout(mesh)
    assert response.status_code == 201 and response.json()["state"] == "completed"
    [(_, record, _)] = payments(mesh).values()
    assert record["status"] == "succeeded"
    assert stock(mesh)["sku-001"] == 998


async def test_void_unreachable_keeps_stock_until_resolved(mesh):
    mesh["transport"].fail("POST", "payments", "/payments/charge", times=2)
    mesh["transport"].fail("POST", "payments", "/payments/", times=2)  # the void as well
    response = await checkout(mesh)
    assert response.status_code == 202
    assert (response.json()["state"], response.json()["outcome"]) == ("payment_unknown", "processing")
    assert stock(mesh)["sku-001"] == 998  # never released while the payment outcome is unknown
    lookup = await mesh["client"].get(f"/api/attempts/{REF}", params={"customer_id": "web-user-1"})
    assert lookup.json()["state"] == "payment_unknown"  # GET did not change anything

    settled = await resolve(mesh)
    assert settled.status_code == 503 and settled.json()["details"]["state"] == "payment_failed"
    assert stock(mesh)["sku-001"] == 1000


async def test_reservation_timeout_is_fenced_by_release(mesh):
    mesh["transport"].fail("POST", "inventory", "/inventory/reservations", after_delivery=True, times=2)
    response = await checkout(mesh)
    assert response.status_code == 503 and response.json()["details"]["state"] == "failed"
    assert stock(mesh)["sku-001"] == 1000 and not payments(mesh)


async def test_failed_release_is_recoverable_by_replay(mesh):
    await payment_policy(mesh, 402)
    mesh["transport"].fail("POST", "inventory", "/inventory/reservations/order-", times=2)
    first = await checkout(mesh)
    assert first.status_code == 202 and first.json()["state"] == "releasing_declined"
    assert stock(mesh)["sku-001"] == 998
    replay = await checkout(mesh)
    assert replay.status_code == 402 and stock(mesh)["sku-001"] == 1000


async def test_resolve_fences_an_unknown_reference(mesh):
    fenced = await resolve(mesh)
    assert fenced.status_code == 409 and fenced.json()["details"]["outcome"] == "abandoned"
    late = await checkout(mesh)
    assert late.status_code == 409 and late.json()["error_code"] == "attempt_abandoned"
    assert not payments(mesh) and stock(mesh)["sku-001"] == 1000


async def stalled(mesh, *events, items=(("sku-001", 2), ("sku-002", 1)), prices=None):
    """Persist an attempt part-way, as if the request driving it had crashed."""
    from services.shared.checkout import CheckoutOrder

    store = mesh["apps"]["orders"].state
    await mesh["client"].get(f"/api/attempts/{REF}", params={"customer_id": "web-user-1"})  # build the saga
    attempts = store.attempts
    order = CheckoutOrder(
        customer_id="web-user-1", items=[{"product_id": p, "quantity": q} for p, q in items]
    )
    view = await attempts.begin(REF, order)
    for event in events:
        extra = {"prices": prices} if event == "charge" else {}
        view = await attempts.advance("web-user-1", REF, event, expected_version=view["version"], **extra)
    return view


async def reserve_for(mesh, view):
    """Take the stock the stalled attempt had reserved before it crashed."""
    from services.shared.checkout import CartReservationInput

    items = [{"product_id": line["product_id"], "quantity": line["quantity"]} for line in view["items"]]
    inventory = mesh["apps"]["inventory"].state.repository
    await inventory.reserve_lines(CartReservationInput(order_id=view["order_id"], items=items))


async def test_resolve_of_a_stalled_charge_voids_instead_of_charging(mesh):
    view = await stalled(mesh, "reserve", "reserved", "charge", prices={"sku-001": 1200, "sku-002": 1200})
    await reserve_for(mesh, view)
    settled = await resolve(mesh)
    assert settled.status_code == 503 and settled.json()["details"]["state"] == "payment_failed"
    [(_, record, _)] = payments(mesh).values()
    assert record["status"] == "voided"
    assert stock(mesh)["sku-001"] == 1000


async def test_resolve_of_a_stalled_reservation_releases_it(mesh):
    await stalled(mesh, "reserve")
    settled = await resolve(mesh)
    assert settled.status_code == 503 and settled.json()["details"]["state"] == "failed"
    assert stock(mesh)["sku-001"] == 1000 and not payments(mesh)


async def test_product_without_a_catalog_price_is_released_not_stranded(mesh):
    view = await stalled(mesh, "reserve", "reserved", items=(("sku-001", 1), ("sku-003", 1)))
    inventory = mesh["apps"]["inventory"].state.repository
    inventory.stock["sku-003"] = 5
    await reserve_for(mesh, view)
    original = inventory.products

    async def catalog_without_sku_003():
        return [product for product in await original() if product["product_id"] != "sku-003"]

    inventory.products = catalog_without_sku_003
    replay = await checkout(mesh, items=(("sku-001", 1), ("sku-003", 1)))
    assert replay.status_code == 503
    assert (replay.json()["error_code"], replay.json()["details"]["state"]) == ("price_unavailable", "failed")
    assert (stock(mesh)["sku-001"], stock(mesh)["sku-003"]) == (1000, 5) and not payments(mesh)


async def test_unknown_products_keep_the_requested_quantity(mesh):
    response = await checkout(mesh, items=(("sku-001", 1), ("sku-999", 3)))
    assert (response.status_code, response.json()["error_code"]) == (409, "product_not_found")
    assert response.json()["details"]["unavailable_items"] == [
        {"product_id": "sku-999", "requested": 3, "available": 0}
    ]


async def test_unexpected_void_status_stays_unknown(mesh):
    mesh["transport"].fail("POST", "payments", "/payments/charge", times=2)
    payments_repo = mesh["apps"]["payments"].state.repository

    async def odd_void(key, order_id):
        return {"payment_id": "void-x", "status": "refunded"}

    payments_repo.void = odd_void
    response = await checkout(mesh)
    assert response.status_code == 202 and response.json()["state"] == "payment_unknown"
    assert stock(mesh)["sku-001"] == 998
