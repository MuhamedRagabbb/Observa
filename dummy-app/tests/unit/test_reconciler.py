"""Background reconciler (Orders): settles stalled checkouts without guessing or charging."""

from datetime import UTC, datetime, timedelta

import pytest

from services.orders.reconciler import Reconciler
from services.shared.attempts import ClaimPolicy
from services.shared.checkout import CartReservationInput, CheckoutOrder
from services.shared.errors import AppError

POLICY = ClaimPolicy(stall_seconds=15, lease_seconds=30, backoff_seconds=5, backoff_cap_seconds=60)
CUSTOMER = "web-user-1"
PRICES = {"sku-001": 1200, "sku-002": 1200}


class Clock:
    def __init__(self):
        self.offset = 16  # past the stall threshold

    def __call__(self):
        return datetime.now(UTC) + timedelta(seconds=self.offset)


@pytest.fixture
async def orders(mesh):
    return mesh["apps"]["orders"].state


def reconciler(orders, clock, **kwargs):
    return Reconciler(orders.attempts, orders.saga, orders.telemetry, policy=POLICY, clock=clock, **kwargs)


async def stalled(mesh, orders, ref, *events, reserve=False):
    """Persist an attempt part-way, as if the request driving it had crashed."""
    order = CheckoutOrder(customer_id=CUSTOMER, items=[{"product_id": "sku-001", "quantity": 2}])
    view = await orders.attempts.begin(ref, order)
    for event in events:
        extra = {"prices": PRICES} if event == "charge" else {}
        view = await orders.attempts.advance(CUSTOMER, ref, event, expected_version=view["version"], **extra)
    if reserve:
        inventory = mesh["apps"]["inventory"].state.repository
        await inventory.reserve_lines(
            CartReservationInput(order_id=view["order_id"], items=[{"product_id": "sku-001", "quantity": 2}])
        )
    return view


def stock(mesh):
    return mesh["apps"]["inventory"].state.repository.stock["sku-001"]


def payments(mesh):
    return mesh["apps"]["payments"].state.repository.rows


def charges_sent(mesh):
    return [r for r in mesh["transport"].requests if r[1] == "payments" and r[2] == "/payments/charge"]


async def state_of(orders, ref):
    return (await orders.attempts.get(CUSTOMER, ref))["state"]


async def test_stalled_charge_is_voided_and_released_never_recharged(mesh, orders):
    await stalled(mesh, orders, "ATT-0001-0001", "reserve", "reserved", "charge", reserve=True)
    report = await reconciler(orders, Clock()).run_cycle()
    assert (report.claimed, report.settled) == (1, 1)
    assert await state_of(orders, "ATT-0001-0001") == "payment_failed"
    [(_, record, _)] = payments(mesh).values()
    assert record["status"] == "voided"
    assert stock(mesh) == 1000 and not charges_sent(mesh)


async def test_stalled_new_attempt_fails_without_touching_stock_or_payments(mesh, orders):
    await stalled(mesh, orders, "ATT-0001-0001")
    await reconciler(orders, Clock()).run_cycle()
    assert await state_of(orders, "ATT-0001-0001") == "failed"
    assert not [r for r in mesh["transport"].requests if r[1] in ("inventory", "payments")]


async def test_paid_attempt_is_completed_not_released(mesh, orders):
    await stalled(mesh, orders, "ATT-0001-0001", "reserve", "reserved", "charge", "charged", reserve=True)
    await reconciler(orders, Clock()).run_cycle()
    assert await state_of(orders, "ATT-0001-0001") == "completed"
    assert stock(mesh) == 998


async def test_attempts_still_in_progress_are_left_alone(mesh, orders):
    await stalled(mesh, orders, "ATT-0001-0001", "reserve")
    clock = Clock()
    clock.offset = 5
    report = await reconciler(orders, clock).run_cycle()
    assert report.claimed == 0 and await state_of(orders, "ATT-0001-0001") == "reserving"


async def test_a_cycle_handles_at_most_one_batch(mesh, orders):
    for ref in ("ATT-0001-0001", "ATT-0002-0002", "ATT-0003-0003"):
        await stalled(mesh, orders, ref)
    report = await reconciler(orders, Clock(), batch_size=2).run_cycle()
    assert report.claimed == 2
    states = [await state_of(orders, ref) for ref in ("ATT-0001-0001", "ATT-0002-0002", "ATT-0003-0003")]
    assert sorted(states) == ["failed", "failed", "received"]


async def test_unconfirmed_payment_keeps_stock_and_is_reported_stuck_once(mesh, orders):
    await stalled(
        mesh, orders, "ATT-0001-0001", "reserve", "reserved", "charge", "charge_unknown", reserve=True
    )
    mesh["transport"].fail("POST", "payments", "/payments/", times=1000)  # the void never gets through
    clock = Clock()
    worker = reconciler(orders, clock, stuck_after=3)
    reports = []
    for _ in range(5):
        reports.append(await worker.run_cycle())
        clock.offset += 61  # past any backoff and lease
    assert await state_of(orders, "ATT-0001-0001") == "payment_unknown"
    assert stock(mesh) == 998  # never released while the payment outcome is unknown
    assert [report.stuck for report in reports] == [0, 0, 1, 0, 0]
    assert mesh["logs"].getvalue().count('"event":"reconciliation_stuck"') == 1
    assert worker.stuck_by_state == {"payment_unknown": 1}


async def test_pool_timeout_ends_the_cycle_instead_of_retrying(mesh, orders):
    await stalled(mesh, orders, "ATT-0001-0001")
    calls = 0

    async def exhausted(policy, *, now):
        nonlocal calls
        calls += 1
        raise AppError(503, "db_pool_timeout", "Database connection capacity unavailable")

    orders.attempts.claim_stalled = exhausted
    report = await reconciler(orders, Clock()).run_cycle()
    assert (report.claimed, report.skipped, calls) == (0, "db_pool_timeout", 1)


async def test_reconciliation_telemetry_is_bounded(mesh, orders):
    view = await stalled(mesh, orders, "ATT-0001-0001", "reserve", "reserved", "charge", reserve=True)
    await reconciler(orders, Clock()).run_cycle()
    logs = mesh["logs"].getvalue()
    assert '"event":"reconciliation_completed"' in logs and "ATT-0001-0001" not in logs
    for resource in mesh["readers"]["orders"].get_metrics_data().resource_metrics:
        for scope in resource.scope_metrics:
            for metric in scope.metrics:
                if metric.name.startswith("orders.reconciliation"):
                    for point in metric.data.data_points:
                        assert set(point.attributes) <= {"state"}
                        assert view["order_id"] not in str(point.attributes)


async def test_reconciliation_runs_in_its_own_trace_linked_to_the_checkout(mesh, orders):
    mesh["transport"].fail(
        "POST", "payments", "/payments/", times=6
    )  # charge and inline void both unconfirmed
    response = await mesh["client"].post(
        "/api/checkout",
        json={
            "customer_id": CUSTOMER,
            "contact": {"name": "Demo name", "email": "demo@example.com"},
            "items": [{"product_id": "sku-001", "quantity": 2}],
        },
        headers={"Idempotency-Key": "ATT-0001-0001"},
    )
    assert response.json()["state"] == "payment_unknown"
    mesh["transport"].faults.clear()
    await reconciler(orders, Clock()).run_cycle()
    assert await state_of(orders, "ATT-0001-0001") == "payment_failed"
    orders.telemetry.traces.force_flush()
    spans = mesh["exporters"]["orders"].get_finished_spans()
    [reconcile] = [s for s in spans if s.name == "checkout.reconcile"]
    [checkout_workflow] = [
        s for s in spans if s.name == "order.workflow" and s.context.trace_id != reconcile.context.trace_id
    ]
    assert reconcile.parent is None and reconcile.context.trace_id != checkout_workflow.context.trace_id
    assert [link.context.trace_id for link in reconcile.links] == [checkout_workflow.context.trace_id]
    completed = [line for line in mesh["logs"].getvalue().splitlines() if "reconciliation_completed" in line]
    assert f'"trace_id":"{reconcile.context.trace_id:032x}"' in completed[0]


async def test_stuck_is_logged_even_when_that_check_fails_on_the_database(mesh, orders):
    await stalled(mesh, orders, "ATT-0001-0001", "reserve")

    async def pool_exhausted(view):
        raise AppError(503, "db_pool_timeout", "Database connection capacity unavailable")

    orders.saga.settle = pool_exhausted
    report = await reconciler(orders, Clock(), stuck_after=1).run_cycle()
    assert (report.skipped, report.stuck) == ("db_pool_timeout", 1)
    assert mesh["logs"].getvalue().count('"event":"reconciliation_stuck"') == 1
