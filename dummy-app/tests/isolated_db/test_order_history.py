"""My Orders (AttemptStore.list_for_customer): one contract, two stores.

The in-memory store runs everywhere; the PostgreSQL store runs with RUN_DB_TESTS=1.
"""

from datetime import datetime

import pytest

from services.shared.checkout import CheckoutOrder
from services.shared.errors import AppError

CUSTOMER = "web-user-1"


def order(customer=CUSTOMER, items=(("sku-001", 2),)):
    return CheckoutOrder(customer_id=customer, items=[{"product_id": p, "quantity": q} for p, q in items])


async def test_a_customer_sees_only_their_own_checkouts_newest_first(attempts):
    first = await attempts.begin("ATT-0001-0001", order(items=(("sku-001", 2), ("sku-002", 1))))
    await attempts.begin("ATT-0002-0002", order(customer="web-user-2"))
    second = await attempts.begin("ATT-0003-0003", order())

    listed = await attempts.list_for_customer(CUSTOMER)

    assert [view["attempt_ref"] for view in listed] == ["ATT-0003-0003", "ATT-0001-0001"]
    assert [view["order_number"] for view in listed] == [second["order_number"], first["order_number"]]
    assert listed[1]["items"] == [
        {"product_id": "sku-001", "quantity": 2},
        {"product_id": "sku-002", "quantity": 1},
    ]
    assert all(isinstance(view["created_at"], datetime) for view in listed)
    assert listed[0]["created_at"] >= listed[1]["created_at"]


async def test_finished_checkouts_show_their_prices_but_resolve_fences_are_not_orders(attempts):
    view = await attempts.begin("ATT-0001-0001", order(items=(("sku-001", 2), ("sku-002", 1))))
    for event, prices in [
        ("reserve", None),
        ("reserved", {"sku-001": 1200, "sku-002": 600}),
        ("charge", None),
    ]:
        view = await attempts.advance(
            CUSTOMER, view["attempt_ref"], event, expected_version=view["version"], prices=prices
        )
    await attempts.advance(CUSTOMER, "ATT-0001-0001", "declined", expected_version=view["version"])
    await attempts.abandon(CUSTOMER, "ATT-0009-0009")  # a checkout that never arrived

    [declined] = await attempts.list_for_customer(CUSTOMER)

    assert (declined["attempt_ref"], declined["state"], declined["total_cents"]) == (
        "ATT-0001-0001",
        "releasing_declined",
        3000,
    )
    assert declined["items"] == [
        {"product_id": "sku-001", "quantity": 2, "unit_price_cents": 1200},
        {"product_id": "sku-002", "quantity": 1, "unit_price_cents": 600},
    ]


async def test_the_list_is_bounded_to_the_newest_checkouts(attempts):
    for ref in ("ATT-0001-0001", "ATT-0002-0002", "ATT-0003-0003"):
        await attempts.begin(ref, order())

    listed = await attempts.list_for_customer(CUSTOMER, limit=2)

    assert [view["attempt_ref"] for view in listed] == ["ATT-0003-0003", "ATT-0002-0002"]


async def test_a_malformed_customer_id_is_refused(attempts):
    with pytest.raises(AppError) as refused:
        await attempts.list_for_customer("bad id")
    assert (refused.value.status, refused.value.code) == (422, "invalid_customer_id")
