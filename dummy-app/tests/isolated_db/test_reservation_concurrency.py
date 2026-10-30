import asyncio
from uuid import uuid4

from services.shared.checkout import CartReservationInput
from services.shared.errors import AppError
from services.shared.models import ReservationInput


async def set_stock(inventory_db, **stock):
    conn = await inventory_db["connect"]()
    try:
        for product, value in stock.items():
            await conn.execute(
                "UPDATE products SET stock=$2 WHERE product_id=$1", product.replace("_", "-"), value
            )
    finally:
        await conn.close()


async def outcome(call):
    try:
        await call
        return "reserved"
    except AppError as exc:
        return exc.code


async def test_overlapping_carts_never_deadlock_or_oversell(inventory, inventory_db):
    await set_stock(inventory_db, sku_003=12, sku_005=12)

    def cart(order):
        lines = [("sku-003", 1), ("sku-005", 1)]
        items = [{"product_id": p, "quantity": q} for p, q in (lines if order else reversed(lines))]
        return CartReservationInput(order_id=f"order-{uuid4()}", items=items)

    carts = [inventory.reserve_lines(cart(n % 2)) for n in range(30)]
    singles = [
        inventory.reserve(ReservationInput(order_id=f"order-{uuid4()}", product_id="sku-005", quantity=1))
        for _ in range(10)
    ]
    results = await asyncio.gather(*(outcome(call) for call in carts + singles))

    assert set(results) <= {"reserved", "insufficient_stock"}  # no deadlock / database_unavailable
    cart_ok, single_ok = results[:30].count("reserved"), results[30:].count("reserved")
    final = {row["product_id"]: row["stock"] for row in await inventory.products()}
    assert final["sku-003"] == 12 - cart_ok
    assert final["sku-005"] == 12 - cart_ok - single_ok
    # Demand for sku-005 (40) exceeds stock (12): it must end exactly at zero, never negative.
    assert final["sku-005"] == 0 and final["sku-003"] >= 0


async def test_parallel_duplicates_create_one_reservation(inventory):
    order_id = f"order-{uuid4()}"
    before = {row["product_id"]: row["stock"] for row in await inventory.products()}
    data = CartReservationInput(order_id=order_id, items=[{"product_id": "sku-001", "quantity": 2}])
    results = await asyncio.gather(*(inventory.reserve_lines(data) for _ in range(10)))
    assert all(result == {"reservation_id": order_id, "status": "reserved"} for result in results)
    after = {row["product_id"]: row["stock"] for row in await inventory.products()}
    assert after["sku-001"] == before["sku-001"] - 2


async def test_release_racing_a_reserve_always_restores_stock(inventory):
    before = {row["product_id"]: row["stock"] for row in await inventory.products()}
    for _ in range(20):
        order_id = f"order-{uuid4()}"
        data = CartReservationInput(
            order_id=order_id,
            items=[{"product_id": "sku-001", "quantity": 1}, {"product_id": "sku-002", "quantity": 2}],
        )
        reserve, release = await asyncio.gather(
            outcome(inventory.reserve_lines(data)), inventory.release(order_id)
        )
        assert reserve in {"reserved", "reservation_released"} and release["status"] == "released"
    after = {row["product_id"]: row["stock"] for row in await inventory.products()}
    assert after == before
