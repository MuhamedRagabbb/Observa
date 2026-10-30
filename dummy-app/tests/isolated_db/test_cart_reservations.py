from uuid import uuid4

import pytest

from services.shared.checkout import CartReservationInput
from services.shared.errors import AppError


def cart(items, order_id=None):
    return CartReservationInput(
        order_id=order_id or f"order-{uuid4()}",
        items=[{"product_id": product, "quantity": quantity} for product, quantity in items],
    )


async def stock(inventory):
    return {row["product_id"]: row["stock"] for row in await inventory.products()}


async def test_cart_reservation_takes_all_lines_or_none(inventory):
    before = await stock(inventory)
    with pytest.raises(AppError) as rejected:
        await inventory.reserve_lines(cart([("sku-001", 2), ("sku-004", 1)]))
    assert (rejected.value.status, rejected.value.code) == (409, "insufficient_stock")
    assert rejected.value.details == {
        "unavailable_items": [{"product_id": "sku-004", "requested": 1, "available": 0}]
    }
    assert await stock(inventory) == before

    result = await inventory.reserve_lines(cart([("sku-002", 1), ("sku-001", 2)]))
    assert result["status"] == "reserved"
    after = await stock(inventory)
    assert (after["sku-001"], after["sku-002"]) == (before["sku-001"] - 2, before["sku-002"] - 1)


async def test_replay_is_idempotent_and_different_input_conflicts(inventory):
    order_id = f"order-{uuid4()}"
    before = await stock(inventory)
    first = await inventory.reserve_lines(cart([("sku-001", 2), ("sku-002", 1)], order_id))
    replay = await inventory.reserve_lines(cart([("sku-002", 1), ("sku-001", 1), ("sku-001", 1)], order_id))
    assert first == replay == {"reservation_id": order_id, "status": "reserved"}
    assert (await stock(inventory))["sku-001"] == before["sku-001"] - 2
    with pytest.raises(AppError) as conflict:
        await inventory.reserve_lines(cart([("sku-001", 3)], order_id))
    assert (conflict.value.status, conflict.value.code) == (409, "idempotency_conflict")


async def test_cart_key_cannot_reuse_a_legacy_reservation_key(inventory):
    from services.shared.models import ReservationInput

    order_id = f"order-{uuid4()}"
    await inventory.reserve(ReservationInput(order_id=order_id, product_id="sku-001", quantity=1))
    with pytest.raises(AppError) as conflict:
        await inventory.reserve_lines(cart([("sku-001", 1)], order_id))
    assert conflict.value.code == "idempotency_conflict"


async def test_unknown_products_are_listed_and_nothing_is_reserved(inventory):
    before = await stock(inventory)
    with pytest.raises(AppError) as missing:
        await inventory.reserve_lines(cart([("sku-001", 1), ("sku-998", 1), ("sku-999", 2)]))
    assert (missing.value.status, missing.value.details) == (
        404,
        {"unknown_products": ["sku-998", "sku-999"]},
    )
    assert await stock(inventory) == before


async def test_release_restores_every_line_once(inventory):
    order_id = f"order-{uuid4()}"
    before = await stock(inventory)
    await inventory.reserve_lines(cart([("sku-001", 3), ("sku-002", 2)], order_id))
    for _ in range(3):
        assert await inventory.release(order_id) == {"reservation_id": order_id, "status": "released"}
    assert await stock(inventory) == before
    with pytest.raises(AppError) as late:
        await inventory.reserve_lines(cart([("sku-001", 3), ("sku-002", 2)], order_id))
    assert late.value.code == "reservation_released"


async def test_release_of_unknown_reservation_fences_out_a_late_reserve(inventory):
    # An ambiguous reserve timeout is compensated by releasing the key first; the
    # tombstone must make the in-flight reserve fail rather than take stock later.
    order_id = f"order-{uuid4()}"
    before = await stock(inventory)
    assert (await inventory.release(order_id))["status"] == "released"
    with pytest.raises(AppError) as late:
        await inventory.reserve_lines(cart([("sku-001", 1)], order_id))
    assert late.value.code == "reservation_released"
    assert await stock(inventory) == before


async def test_legacy_reserve_and_release_are_unchanged(inventory):
    from services.shared.models import ReservationInput

    order_id = f"order-{uuid4()}"
    before = await stock(inventory)
    legacy = ReservationInput(order_id=order_id, product_id="sku-002", quantity=4)
    assert await inventory.reserve(legacy) == {"reservation_id": order_id, "status": "reserved"}
    assert (await stock(inventory))["sku-002"] == before["sku-002"] - 4
    assert (await inventory.release(order_id))["status"] == "released"
    assert await stock(inventory) == before


async def test_legacy_paths_work_on_the_unmigrated_schema(unmigrated_inventory):
    from services.shared.models import ReservationInput

    order_id = f"order-{uuid4()}"
    before = await stock(unmigrated_inventory)
    await unmigrated_inventory.reserve(ReservationInput(order_id=order_id, product_id="sku-001", quantity=2))
    assert (await unmigrated_inventory.release(order_id))["status"] == "released"
    assert (await unmigrated_inventory.release(f"order-{uuid4()}"))["status"] == "released"
    assert await stock(unmigrated_inventory) == before


async def test_legacy_reserve_cannot_reuse_a_cart_key(inventory):
    from services.shared.models import ReservationInput

    order_id = f"order-{uuid4()}"
    await inventory.reserve_lines(cart([("sku-001", 2)], order_id))
    reserved = await stock(inventory)
    with pytest.raises(AppError) as conflict:
        await inventory.reserve(ReservationInput(order_id=order_id, product_id="sku-001", quantity=2))
    assert conflict.value.code == "idempotency_conflict"
    assert await stock(inventory) == reserved


async def test_cart_reserve_on_unmigrated_schema_reports_missing_capability(unmigrated_inventory):
    before = await stock(unmigrated_inventory)
    with pytest.raises(AppError) as missing:
        await unmigrated_inventory.reserve_lines(cart([("sku-001", 1)]))
    assert (missing.value.status, missing.value.code) == (503, "cart_reservations_unavailable")
    assert await stock(unmigrated_inventory) == before
