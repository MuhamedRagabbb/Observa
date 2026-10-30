import asyncio
from uuid import uuid4

import pytest

from services.shared.errors import AppError
from services.shared.models import PaymentInput
from tests.isolated_db.conftest import open_repository


def payment(order_id=None, amount=5400):
    order_id = order_id or f"order-{uuid4()}"
    return PaymentInput(
        order_id=order_id, idempotency_key=order_id, customer_id="web-user-1", amount_cents=amount
    )


@pytest.fixture
async def payments(payments_db):
    from services.shared.repositories import PaymentRepository

    async with open_repository(payments_db, PaymentRepository, migrate=True) as repository:
        yield repository


async def test_void_fences_a_key_so_a_late_charge_can_never_succeed(payments):
    data = payment()
    assert await payments.lookup(data.idempotency_key) is None  # absent is not proof of anything yet
    voided = await payments.void(data.idempotency_key, data.order_id)
    assert voided["status"] == "voided"
    assert (await payments.lookup(data.idempotency_key))["status"] == "voided"
    assert (await payments.void(data.idempotency_key, data.order_id)) == voided
    with pytest.raises(AppError) as late:
        await payments.charge(data, decline=False)
    assert (late.value.status, late.value.code) == (409, "payment_voided")
    with pytest.raises(AppError) as replay:
        await payments.existing(data)
    assert replay.value.code == "payment_voided"


async def test_void_after_a_charge_reports_the_charge_and_changes_nothing(payments):
    data = payment()
    charged = await payments.charge(data, decline=False)
    assert (
        await payments.void(data.idempotency_key, data.order_id)
        == charged
        == {
            "payment_id": charged["payment_id"],
            "status": "succeeded",
        }
    )
    assert (await payments.lookup(data.idempotency_key))["status"] == "succeeded"


async def test_void_for_a_different_order_conflicts(payments):
    data = payment()
    await payments.void(data.idempotency_key, data.order_id)
    with pytest.raises(AppError) as conflict:
        await payments.void(data.idempotency_key, f"order-{uuid4()}")
    assert conflict.value.code == "idempotency_conflict"


async def test_racing_charge_and_void_always_agree_with_lookup(payments):
    async def charge(data):
        try:
            return (await payments.charge(data, decline=False))["status"]
        except AppError as exc:
            return exc.code

    for _ in range(25):
        data = payment()
        charged, voided = await asyncio.gather(
            charge(data), payments.void(data.idempotency_key, data.order_id)
        )
        final = (await payments.lookup(data.idempotency_key))["status"]
        assert (charged, voided["status"], final) in {
            ("succeeded", "succeeded", "succeeded"),
            ("payment_voided", "voided", "voided"),
        }


async def test_unmigrated_schema_keeps_legacy_charges_and_refuses_void_clearly(payments_db):
    from services.shared.repositories import PaymentRepository

    async with open_repository(payments_db, PaymentRepository, migrate=False) as legacy:
        data = payment()
        charged = await legacy.charge(data, decline=False)
        assert await legacy.existing(data) == charged
        assert (await legacy.lookup(data.idempotency_key))["status"] == "succeeded"
        with pytest.raises(AppError) as missing:
            await legacy.void(f"order-{uuid4()}", f"order-{uuid4()}")
        assert (missing.value.status, missing.value.code) == (503, "payment_void_unavailable")
