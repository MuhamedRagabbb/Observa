"""Reconciler in the Orders lifespan, on an isolated orders database (in-memory mesh for the rest).

Proves recovery after a crash and restart, under F1 pool pressure and with two reconciler
processes: no double charge, no double reservation, and no stock released while a payment
outcome is unknown.
"""

import asyncio
import json
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from services.shared.attempts import AttemptStore
from services.shared.checkout import TERMINAL_STATES, CartReservationInput, CheckoutOrder
from services.shared.config import Settings
from services.shared.database import Database
from services.shared.migrations import apply_migrations
from services.shared.models import PaymentInput
from services.shared.repositories import OrderRepository
from tests.conftest import build_mesh
from tests.isolated_db.conftest import MIGRATIONS

CUSTOMER = "web-user-1"
PRICES = {"sku-001": 1200}
FAST = {"reconciler_interval": 0.05}


@asynccontextmanager
async def orders_mesh(
    orders_db, *, keep, enabled, pool_max=8, pool_timeout=5, migrations=None, resume_after=0
):
    conn = await orders_db["connect"]()
    await apply_migrations(conn, migrations or MIGRATIONS / "orders")
    await conn.close()
    settings = Settings(dsn=orders_db["dsn"], otlp_endpoint="", pool_max=pool_max, pool_timeout=pool_timeout)

    def orders_factory(runtime, tel):
        db = Database(settings, tel, runtime)
        return db, OrderRepository(db)

    async with build_mesh(
        orders_factory=orders_factory,
        attempts_factory=AttemptStore,
        saga_resume_after=resume_after,
        keep=keep,
        reconciler_enabled=enabled,
        **FAST,
    ) as mesh:
        yield mesh


def order():
    return CheckoutOrder(customer_id=CUSTOMER, items=[{"product_id": "sku-001", "quantity": 2}])


async def crash_after(mesh, ref, *events, reserved=False, charged=False):
    """Persist an attempt part-way and leave its side effects behind, as a crashed request would."""
    attempts = mesh["apps"]["orders"].state.attempts
    view = await attempts.begin(ref, order())
    for event in events:
        extra = {"prices": PRICES} if event == "charge" else {}
        view = await attempts.advance(CUSTOMER, ref, event, expected_version=view["version"], **extra)
    if reserved:
        await mesh["apps"]["inventory"].state.repository.reserve_lines(
            CartReservationInput(order_id=view["order_id"], items=[{"product_id": "sku-001", "quantity": 2}])
        )
    if charged:  # the charge committed, but its response never reached Orders
        await mesh["apps"]["payments"].state.repository.charge(
            PaymentInput(
                order_id=view["order_id"],
                idempotency_key=view["order_id"],
                customer_id=CUSTOMER,
                amount_cents=2400,
            ),
            decline=False,
        )
    return view


async def settled(mesh, refs, within=10):
    attempts = mesh["apps"]["orders"].state.attempts
    deadline = time.monotonic() + within
    while True:
        states = {ref: (await attempts.get(CUSTOMER, ref))["state"] for ref in refs}
        if all(state in TERMINAL_STATES for state in states.values()) or time.monotonic() > deadline:
            return states
        await asyncio.sleep(0.05)


CRASHES = {
    "ATT-0001-0001": ((), {}, "failed"),
    "ATT-0002-0002": (("reserve",), {}, "failed"),
    "ATT-0003-0003": (("reserve", "reserved"), {"reserved": True}, "failed"),
    "ATT-0004-0004": (("reserve", "reserved", "charge"), {"reserved": True}, "payment_failed"),
    "ATT-0005-0005": (("reserve", "reserved", "charge"), {"reserved": True, "charged": True}, "completed"),
    "ATT-0006-0006": (
        ("reserve", "reserved", "charge", "charge_unknown"),
        {"reserved": True},
        "payment_failed",
    ),
    "ATT-0007-0007": (
        ("reserve", "reserved", "charge", "charged"),
        {"reserved": True, "charged": True},
        "completed",
    ),
    "ATT-0008-0008": (("reserve", "reserve_unknown"), {}, "failed"),
}


async def test_restart_settles_attempts_left_at_every_step_by_a_crash(orders_db):
    keep = {}
    async with orders_mesh(orders_db, keep=keep, enabled=False) as before:
        legacy = await orders_db["connect"]()
        await legacy.execute(
            "INSERT INTO orders (order_id, product_id, quantity, customer_id, status) "
            "VALUES ('order-legacy', 'sku-001', 1, 'legacy', 'pending')"
        )
        for ref, (events, effects, _) in CRASHES.items():
            await crash_after(before, ref, *events, **effects)
    async with orders_mesh(orders_db, keep=keep, enabled=True) as after:  # the restarted service
        states = await settled(after, CRASHES)
        charges = [r for r in after["transport"].requests if r[2] == "/payments/charge"]
    assert states == {ref: expected for ref, (_, _, expected) in CRASHES.items()}
    assert await legacy.fetchval("SELECT status FROM orders WHERE order_id='order-legacy'") == "pending"
    await legacy.close()
    assert not charges  # recovery never sends a new charge
    payments = keep["MemoryPayments"].rows
    assert sorted(record["status"] for _, record, _ in payments.values()) == [
        "succeeded",
        "succeeded",
        "voided",
        "voided",
    ]
    # Exactly the two paid orders still hold stock; everything else was released once.
    assert keep["MemoryInventory"].stock["sku-001"] == 1000 - 2 * 2


async def test_pool_pressure_costs_one_timeout_per_cycle_then_recovers(orders_db):
    keep = {}
    async with orders_mesh(
        orders_db, keep=keep, enabled=True, pool_max=2, pool_timeout=0.2, resume_after=1
    ) as mesh:
        await crash_after(mesh, "ATT-0001-0001", "reserve", "reserved", reserved=True)
        pool = mesh["apps"]["orders"].state.db.pool
        held = [await pool.acquire(), await pool.acquire()]  # F1: every connection is busy
        await asyncio.sleep(1.6)  # the attempt becomes stalled while the pool is exhausted
        for connection in held:
            await pool.release(connection)
        states = await settled(mesh, ["ATT-0001-0001"])
    assert states == {"ATT-0001-0001": "failed"}
    assert keep["MemoryInventory"].stock["sku-001"] == 1000
    timeouts = [
        line
        for line in mesh["logs"].getvalue().splitlines()
        if '"event":"db_pool_timeout"' in line and "attempt.claim" in line
    ]
    # One failed claim ends the cycle: at most one timeout per 0.25 s cycle, never a batch of 20.
    assert 1 <= len(timeouts) <= 8


async def test_reconciler_stays_idle_without_migration_003(orders_db, tmp_path):
    for path in sorted((MIGRATIONS / "orders").glob("00[12]_*.sql")):
        (tmp_path / path.name).write_bytes(path.read_bytes())
    async with orders_mesh(orders_db, keep={}, enabled=True, migrations=tmp_path) as mesh:
        await asyncio.sleep(0.2)
        health = await mesh["client"].get("/health")
    logs = mesh["logs"].getvalue()
    assert logs.count('"event":"reconciler_disabled"') == 1
    assert '"event":"reconciliation_cycle_failed"' not in logs
    assert health.status_code == 200


@asynccontextmanager
async def served(app):
    """Serve an already-started in-memory app over real HTTP on a random loopback port."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="error"))
    task = asyncio.create_task(server.serve())
    while not server.started:  # noqa: ASYNC110 - uvicorn exposes only a flag
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


async def test_two_reconciler_processes_never_share_or_repeat_work(orders_db):
    refs = [f"ATT-{n:04d}-{n:04d}" for n in range(1, 13)]
    keep = {}
    async with orders_mesh(orders_db, keep=keep, enabled=False) as mesh:
        for n, ref in enumerate(refs):
            if n % 3 == 0:
                await crash_after(mesh, ref, "reserve", "reserved", "charge", reserved=True)  # → voided
            elif n % 3 == 1:
                await crash_after(mesh, ref, "reserve", "reserved", "charge", reserved=True, charged=True)
            else:
                await crash_after(mesh, ref, "reserve", "reserved", reserved=True)  # → released
        payments = keep["MemoryPayments"]
        original_void, charges = payments.void, []

        async def slow_void(key, order_id):  # keeps both processes busy at the same time
            await asyncio.sleep(0.3)
            return await original_void(key, order_id)

        async def counted_charge(data, decline):
            charges.append(data.order_id)
            raise AssertionError("recovery must never charge")

        payments.void, payments.charge = slow_void, counted_charge
        await asyncio.sleep(1.1)  # every attempt is now stalled
        async with (
            served(mesh["apps"]["inventory"]) as inventory_url,
            served(mesh["apps"]["payments"]) as payments_url,
        ):
            root = Path(__file__).resolve().parents[2]
            workers = [
                await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "tests.isolated_db.reconciler_worker",
                    orders_db["dsn"],
                    inventory_url,
                    payments_url,
                    cwd=root,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                for _ in range(2)
            ]
            outputs = await asyncio.wait_for(asyncio.gather(*(w.communicate() for w in workers)), 60)
        states = await settled(mesh, refs, within=1)
        conn = await orders_db["connect"]()
        checks = await conn.fetch(
            "SELECT attempt_ref, check_count FROM checkout_attempts ORDER BY attempt_ref"
        )
        await conn.close()
    results = []
    for worker, (stdout, stderr) in zip(workers, outputs, strict=True):
        assert worker.returncode == 0, stderr.decode()[-2000:]
        [line] = [line for line in stdout.decode().splitlines() if line.startswith("RESULT ")]
        results.append(json.loads(line[7:])["claimed"])
    expected = ["payment_failed", "completed", "failed"] * 4
    assert [states[ref] for ref in refs] == expected
    assert sum(results) == len(refs) and min(results) >= 1  # both processes worked, nothing twice
    assert {row["check_count"] for row in checks} == {1}
    assert not charges
    assert (
        sorted(record["status"] for _, record, _ in payments.rows.values())
        == ["succeeded"] * 4 + ["voided"] * 4
    )
    assert keep["MemoryInventory"].stock["sku-001"] == 1000 - 4 * 2
