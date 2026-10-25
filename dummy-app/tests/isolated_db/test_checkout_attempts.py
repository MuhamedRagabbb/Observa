import re

import pytest

from services.shared.checkout import CheckoutInput
from services.shared.errors import AppError
from tests.isolated_db.conftest import open_repository

REF = "ATT-7F3A-0915"


def checkout(items=(("sku-001", 2), ("sku-002", 1)), customer="web-user-1", name="Demo name"):
    return CheckoutInput(
        customer_id=customer,
        contact={"name": name, "email": "demo@example.com"},
        items=[{"product_id": p, "quantity": q} for p, q in items],
    )


@pytest.fixture
async def attempts(orders_db):
    from services.shared.attempts import AttemptStore

    async with open_repository(orders_db, AttemptStore, migrate=True) as store:
        yield store


async def test_begin_persists_a_received_attempt_before_side_effects(attempts):
    attempt = await attempts.begin(REF, checkout())
    assert attempt["state"] == "received" and attempt["version"] == 0 and attempt["replayed"] is False
    assert re.fullmatch(r"order-[a-f0-9-]{36}", attempt["order_id"])
    assert re.fullmatch(r"OBS-[0-9]{4,}-[0-9A-F]{4}", attempt["order_number"])
    assert attempt["items"] == [
        {"product_id": "sku-001", "quantity": 2},
        {"product_id": "sku-002", "quantity": 1},
    ]
    assert await attempts.get("web-user-1", REF) == {**attempt, "replayed": False}


async def test_replay_returns_the_same_attempt_and_different_input_conflicts(attempts):
    first = await attempts.begin(REF, checkout())
    replay = await attempts.begin(REF, checkout(items=(("sku-002", 1), ("sku-001", 2)), name="Someone else"))
    assert replay["order_id"] == first["order_id"] and replay["replayed"] is True
    with pytest.raises(AppError) as conflict:
        await attempts.begin(REF, checkout(items=(("sku-001", 3),)))
    assert (conflict.value.status, conflict.value.code) == (409, "idempotency_conflict")


async def test_attempt_keys_are_scoped_per_customer(attempts):
    mine = await attempts.begin(REF, checkout(customer="web-user-1"))
    theirs = await attempts.begin(REF, checkout(customer="web-user-2", items=(("sku-003", 1),)))
    assert mine["order_id"] != theirs["order_id"]


async def test_advance_is_a_compare_and_set_through_the_state_contract(attempts):
    attempt = await attempts.begin(REF, checkout())
    reserving = await attempts.advance("web-user-1", REF, "reserve", expected_version=0)
    assert (reserving["state"], reserving["version"]) == ("reserving", 1)

    with pytest.raises(AppError) as stale:
        await attempts.advance("web-user-1", REF, "reserved", expected_version=0)
    assert (stale.value.status, stale.value.code) == (409, "stale_attempt")

    with pytest.raises(AppError) as unsafe:
        await attempts.advance("web-user-1", REF, "charged", expected_version=1)
    assert unsafe.value.code == "invalid_transition"
    assert (await attempts.get("web-user-1", REF))["state"] == "reserving"

    failed = await attempts.advance(
        "web-user-1", REF, "out_of_stock", expected_version=1, error_code="insufficient_stock"
    )
    assert (failed["state"], failed["version"], failed["error_code"]) == (
        "not_placed",
        2,
        "insufficient_stock",
    )
    assert failed["order_id"] == attempt["order_id"]


async def test_unknown_attempt_cannot_be_advanced(attempts):
    with pytest.raises(AppError) as missing:
        await attempts.advance("web-user-1", "ATT-0000-0000", "reserve", expected_version=0)
    assert (missing.value.status, missing.value.code) == (404, "attempt_not_found")


async def test_committed_state_survives_a_service_restart(orders_db):
    from services.shared.attempts import AttemptStore

    async with open_repository(orders_db, AttemptStore, migrate=True) as store:
        await store.begin(REF, checkout())
        await store.advance("web-user-1", REF, "reserve", expected_version=0)
        await store.advance("web-user-1", REF, "reserved", expected_version=1)
        await store.advance("web-user-1", REF, "charge", expected_version=2)
        await store.advance("web-user-1", REF, "charge_unknown", expected_version=3)
    async with open_repository(orders_db, AttemptStore, migrate=True) as restarted:
        attempt = await restarted.get("web-user-1", REF)
        assert (attempt["state"], attempt["version"]) == ("payment_unknown", 4)
        # An unknown payment is never released or failed directly after a restart either.
        with pytest.raises(AppError) as unsafe:
            await restarted.advance("web-user-1", REF, "released", expected_version=4)
        assert unsafe.value.code == "invalid_transition"


async def test_abandon_fences_an_absent_attempt_against_a_late_begin(attempts):
    fenced = await attempts.abandon("web-user-1", REF)
    assert (fenced["state"], fenced["order_id"], fenced["items"]) == ("abandoned", None, [])
    assert (await attempts.abandon("web-user-1", REF))["version"] == fenced["version"]
    with pytest.raises(AppError) as late:
        await attempts.begin(REF, checkout())
    assert (late.value.status, late.value.code) == (409, "attempt_abandoned")


async def test_abandon_never_cancels_a_real_attempt(attempts):
    started = await attempts.begin(REF, checkout())
    await attempts.advance("web-user-1", REF, "reserve", expected_version=0)
    found = await attempts.abandon("web-user-1", REF)
    assert (found["state"], found["order_id"]) == ("reserving", started["order_id"])


async def capture(call):
    try:
        return await call
    except AppError as exc:
        return exc.code


async def test_parallel_begins_create_exactly_one_attempt(attempts):
    import asyncio

    same = await asyncio.gather(*(attempts.begin(REF, checkout()) for _ in range(10)))
    assert len({attempt["order_id"] for attempt in same}) == 1
    assert [attempt["replayed"] for attempt in same].count(False) == 1

    other = "ATT-1111-2222"
    racing = await asyncio.gather(
        *(capture(attempts.begin(other, checkout(items=((f"sku-00{n}", 1),)))) for n in range(1, 6))
    )
    created = [result for result in racing if isinstance(result, dict)]
    assert len(created) == 1 and racing.count("idempotency_conflict") == 4


async def test_parallel_advances_have_exactly_one_winner(attempts):
    import asyncio

    await attempts.begin(REF, checkout())
    results = await asyncio.gather(
        *(capture(attempts.advance("web-user-1", REF, "reserve", expected_version=0)) for _ in range(8))
    )
    winners = [result for result in results if isinstance(result, dict)]
    assert len(winners) == 1 and results.count("stale_attempt") == 7
    assert (await attempts.get("web-user-1", REF))["version"] == 1


async def test_begin_racing_abandon_never_both_places_and_fences(attempts):
    import asyncio

    for n in range(20):
        ref = f"ATT-AB{n:02d}-{n:04d}"
        begun, resolved = await asyncio.gather(
            capture(attempts.begin(ref, checkout())), attempts.abandon("web-user-1", ref)
        )
        if begun == "attempt_abandoned":
            assert resolved["state"] == "abandoned"
        else:
            assert resolved["state"] == "received" and resolved["order_id"] == begun["order_id"]


@pytest.mark.parametrize(("customer", "ref"), [("web-user-1", "bad-ref"), ("bad\ncustomer", REF), ("", REF)])
async def test_malformed_keys_are_rejected_before_touching_the_database(attempts, customer, ref):
    with pytest.raises(AppError) as invalid:
        await attempts.abandon(customer, ref)
    assert (invalid.value.status, invalid.value.code) == (422, "invalid_attempt_key")
    with pytest.raises(AppError) as invalid_begin:
        await attempts.begin("bad-ref", checkout())
    assert invalid_begin.value.status == 422


async def test_two_service_processes_racing_on_one_key_agree(orders_db):
    import asyncio

    from services.shared.attempts import AttemptStore

    async with (
        open_repository(orders_db, AttemptStore, migrate=True) as first,
        open_repository(orders_db, AttemptStore, migrate=True) as second,
    ):
        for n in range(15):
            ref = f"ATT-CD{n:02d}-{n:04d}"
            begun, resolved = await asyncio.gather(
                capture(first.begin(ref, checkout())), second.abandon("web-user-1", ref)
            )
            final = await first.get("web-user-1", ref)
            assert final["state"] == resolved["state"]
            assert (begun == "attempt_abandoned") == (final["state"] == "abandoned")


async def test_charge_step_persists_server_prices_and_failures_keep_details(attempts):
    await attempts.begin(REF, checkout())
    await attempts.advance("web-user-1", REF, "reserve", expected_version=0)
    await attempts.advance("web-user-1", REF, "reserved", expected_version=1)
    charging = await attempts.advance(
        "web-user-1", REF, "charge", expected_version=2, prices={"sku-001": 1200, "sku-002": 1800}
    )
    assert charging["total_cents"] == 4200
    assert charging["items"] == [
        {"product_id": "sku-001", "quantity": 2, "unit_price_cents": 1200},
        {"product_id": "sku-002", "quantity": 1, "unit_price_cents": 1800},
    ]
    assert charging["updated_at"] is not None

    other = "ATT-2222-3333"
    await attempts.begin(other, checkout(items=(("sku-004", 1),)))
    await attempts.advance("web-user-1", other, "reserve", expected_version=0)
    unavailable = {"unavailable_items": [{"product_id": "sku-004", "requested": 1, "available": 0}]}
    placed = await attempts.advance(
        "web-user-1",
        other,
        "out_of_stock",
        expected_version=1,
        error_code="insufficient_stock",
        details=unavailable,
    )
    assert (placed["state"], placed["details"]) == ("not_placed", unavailable)
    assert (await attempts.get("web-user-1", other))["details"] == unavailable


async def test_prices_must_cover_every_line(attempts):
    await attempts.begin(REF, checkout())
    for version, event in enumerate(("reserve", "reserved")):
        await attempts.advance("web-user-1", REF, event, expected_version=version)
    with pytest.raises(AppError) as missing:
        await attempts.advance("web-user-1", REF, "charge", expected_version=2, prices={"sku-001": 1200})
    assert missing.value.code == "price_unavailable"
    assert (await attempts.get("web-user-1", REF))["state"] == "reserved"
