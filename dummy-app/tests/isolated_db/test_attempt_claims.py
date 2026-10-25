"""Reconciler claims (AttemptStore.claim_stalled / release_claim): one contract, two stores.

The in-memory store runs everywhere; the PostgreSQL store runs with RUN_DB_TESTS=1.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from services.shared.attempts import ClaimPolicy
from services.shared.checkout import CheckoutOrder
from tests.isolated_db.conftest import open_repository

POLICY = ClaimPolicy(stall_seconds=15, lease_seconds=30, backoff_seconds=5, backoff_cap_seconds=60)
CUSTOMER = "web-user-1"


def order(items=(("sku-001", 2),)):
    return CheckoutOrder(customer_id=CUSTOMER, items=[{"product_id": p, "quantity": q} for p, q in items])


@pytest.fixture
async def postgres_attempts(orders_db):
    from services.shared.attempts import AttemptStore

    async with open_repository(orders_db, AttemptStore, migrate=True) as store:
        yield store


@pytest.fixture
def memory_attempts():
    from services.shared.config import Settings
    from services.shared.runtime import Runtime
    from services.shared.telemetry import Telemetry
    from tests.conftest import MemoryAttempts, MemoryDatabase

    telemetry = Telemetry("orders-test", Settings(otlp_endpoint=""))
    yield MemoryAttempts(MemoryDatabase(Runtime(telemetry), telemetry))
    telemetry.close()


@pytest.fixture(params=["memory", "postgres"])
def attempts(request):
    """Sync on purpose: the isolated database must start outside the test's event loop."""
    return request.getfixturevalue(f"{request.param}_attempts")


async def started(attempts, ref, *events):
    view = await attempts.begin(ref, order())
    for event in events:
        view = await attempts.advance(CUSTOMER, ref, event, expected_version=view["version"])
    return view


def later(seconds):
    return datetime.now(UTC) + timedelta(seconds=seconds)


async def test_only_attempts_without_recent_progress_are_claimed(attempts):
    await started(attempts, "ATT-0001-0001", "reserve")
    assert await attempts.claim_stalled(POLICY, now=later(5)) is None  # a request may still drive it
    claimed = await attempts.claim_stalled(POLICY, now=later(16))
    assert (claimed["attempt_ref"], claimed["state"], claimed["check_count"]) == (
        "ATT-0001-0001",
        "reserving",
        1,
    )


async def test_terminal_and_abandoned_attempts_are_never_claimed(attempts):
    await started(attempts, "ATT-0001-0001", "reserve", "out_of_stock")
    await attempts.abandon(CUSTOMER, "ATT-0002-0002")
    assert await attempts.claim_stalled(POLICY, now=later(3600)) is None


async def test_a_claimed_attempt_is_leased_until_released(attempts):
    await started(attempts, "ATT-0001-0001", "reserve")
    claimed = await attempts.claim_stalled(POLICY, now=later(16))
    assert await attempts.claim_stalled(POLICY, now=later(16 + 29)) is None  # leased, not just backed off
    await attempts.release_claim(claimed)
    assert (await attempts.claim_stalled(POLICY, now=later(16 + 29)))["check_count"] == 2


async def test_rechecks_back_off_exponentially_up_to_the_cap(attempts):
    await started(attempts, "ATT-0001-0001", "reserve")
    at = 16
    await attempts.release_claim(await attempts.claim_stalled(POLICY, now=later(at)))
    for wait in (5, 10, 20, 40, 60, 60):
        assert await attempts.claim_stalled(POLICY, now=later(at + wait - 1)) is None
        claimed = await attempts.claim_stalled(POLICY, now=later(at + wait))
        assert claimed is not None
        await attempts.release_claim(claimed)
        at += wait
    assert claimed["check_count"] == 7


async def test_an_expired_lease_cannot_release_the_newer_claim(attempts):
    await started(attempts, "ATT-0001-0001", "reserve")
    first = await attempts.claim_stalled(POLICY, now=later(16))
    second = await attempts.claim_stalled(POLICY, now=later(16 + 31))  # first lease expired
    assert second["check_count"] == 2
    await attempts.release_claim(first)  # the slow first owner finishing late
    assert await attempts.claim_stalled(POLICY, now=later(16 + 31 + 20)) is None


async def test_claims_cover_each_due_attempt_once_per_round(attempts):
    for ref in ("ATT-0001-0001", "ATT-0002-0002", "ATT-0003-0003"):
        await started(attempts, ref, "reserve")
    claimed = [await attempts.claim_stalled(POLICY, now=later(16)) for _ in range(4)]
    assert sorted(view["attempt_ref"] for view in claimed[:3]) == [
        "ATT-0001-0001",
        "ATT-0002-0002",
        "ATT-0003-0003",
    ]
    assert claimed[3] is None


async def test_a_row_locked_by_another_claimer_is_skipped_not_waited_on(postgres_attempts, orders_db):
    for ref in ("ATT-0001-0001", "ATT-0002-0002"):
        await started(postgres_attempts, ref, "reserve")
    other = await orders_db["connect"]()
    try:
        async with other.transaction():  # another reconciler, mid-claim
            await other.execute(
                "SELECT 1 FROM checkout_attempts WHERE attempt_ref='ATT-0001-0001' FOR UPDATE"
            )
            claimed = await asyncio.wait_for(postgres_attempts.claim_stalled(POLICY, now=later(16)), 5)
            assert claimed["attempt_ref"] == "ATT-0002-0002"
            assert await asyncio.wait_for(postgres_attempts.claim_stalled(POLICY, now=later(16)), 5) is None
    finally:
        await other.close()


async def test_stuck_counts_open_attempts_by_state_after_enough_checks(attempts):
    await started(attempts, "ATT-0001-0001", "reserve")
    await started(attempts, "ATT-0002-0002", "reserve", "out_of_stock")
    at = 16
    for _ in range(3):
        await attempts.release_claim(await attempts.claim_stalled(POLICY, now=later(at)))
        at += 61
    assert await attempts.count_stuck(3) == {"reserving": 1}
    assert await attempts.count_stuck(4) == {}
