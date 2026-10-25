"""Checkout saga with the real AttemptStore on an isolated orders database (mesh for the rest)."""

import asyncio
import time
from contextlib import asynccontextmanager

import pytest

from services.shared.attempts import AttemptStore
from services.shared.checkout import TERMINAL_STATES, CheckoutOrder
from services.shared.config import Settings
from services.shared.database import Database
from services.shared.migrations import apply_migrations
from services.shared.models import RuntimeUpdate
from services.shared.repositories import OrderRepository
from tests.conftest import build_mesh
from tests.isolated_db.conftest import MIGRATIONS

REF = "ATT-7F3A-0915"
CONTACT = {"name": "Demo name", "email": "demo@example.com"}
HOLDS_STOCK = {"reserved", "charging", "payment_unknown", "completing", "completed"}


def body(items=(("sku-001", 2), ("sku-002", 1)), customer="web-user-1"):
    return {
        "customer_id": customer,
        "contact": CONTACT,
        "items": [{"product_id": p, "quantity": q} for p, q in items],
    }


@asynccontextmanager
async def pg_mesh(orders_db, *, pool_max=8, pool_timeout=5, resume_after=0):
    conn = await orders_db["connect"]()
    await apply_migrations(conn, MIGRATIONS / "orders")
    await conn.close()
    settings = Settings(dsn=orders_db["dsn"], otlp_endpoint="", pool_max=pool_max, pool_timeout=pool_timeout)

    def orders_factory(runtime, tel):
        db = Database(settings, tel, runtime)
        return db, OrderRepository(db)

    async with build_mesh(
        orders_factory=orders_factory, attempts_factory=AttemptStore, saga_resume_after=resume_after
    ) as mesh:
        yield mesh


async def checkout(mesh, ref=REF, **kwargs):
    return await mesh["client"].post("/api/checkout", json=body(**kwargs), headers={"Idempotency-Key": ref})


async def status(mesh, ref=REF):
    return await mesh["client"].get(f"/api/attempts/{ref}", params={"customer_id": "web-user-1"})


async def test_completed_checkout_is_durable(orders_db):
    async with pg_mesh(orders_db) as mesh:
        placed = await checkout(mesh)
        assert placed.status_code == 201
        stored = (await status(mesh)).json()
        assert (stored["state"], stored["total_cents"], stored["order_number"]) == (
            "completed",
            3600,
            placed.json()["order_number"],
        )


async def test_replay_resumes_an_attempt_left_mid_saga_by_a_crash(orders_db):
    async with pg_mesh(orders_db) as mesh:
        # Simulate a crash right after "reserving" was persisted, before the reserve call returned.
        store = AttemptStore(mesh["apps"]["orders"].state.db)
        order = CheckoutOrder(customer_id="web-user-1", items=body()["items"])
        await store.begin(REF, order)
        await store.advance("web-user-1", REF, "reserve", expected_version=0)

        resumed = await checkout(mesh)
        assert resumed.status_code == 201 and resumed.json()["state"] == "completed"
        assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 998
        assert len(mesh["apps"]["payments"].state.repository.rows) == 1


async def test_recent_attempts_are_not_re_driven_by_a_replay(orders_db):
    async with pg_mesh(orders_db, resume_after=60) as mesh:
        store = AttemptStore(mesh["apps"]["orders"].state.db)
        await store.begin(REF, CheckoutOrder(customer_id="web-user-1", items=body()["items"]))
        await store.advance("web-user-1", REF, "reserve", expected_version=0)
        replay = await checkout(mesh)
        assert replay.status_code == 202 and replay.json()["state"] == "reserving"
        assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000  # nothing re-sent


async def test_duplicate_concurrent_submissions_charge_and_reserve_once(orders_db):
    async with pg_mesh(orders_db, resume_after=60) as mesh:
        responses = await asyncio.gather(*(checkout(mesh) for _ in range(10)))
        assert {response.status_code for response in responses} <= {201, 202}
        assert (await status(mesh)).json()["state"] == "completed"
        assert len(mesh["apps"]["payments"].state.repository.rows) == 1
        assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 998


async def test_f1_pool_pressure_never_loses_or_double_counts_stock(orders_db):
    async with pg_mesh(orders_db, pool_max=2, pool_timeout=0.2) as mesh:
        runtime = mesh["apps"]["orders"].state.runtime
        await runtime.update(RuntimeUpdate(expires_at=time.time() + 30, db_hold_seconds=0.6))
        refs = [f"ATT-F1{n:02d}-{n:04d}" for n in range(12)]
        responses = await asyncio.gather(*(checkout(mesh, ref=ref, items=(("sku-001", 1),)) for ref in refs))
        await runtime.update(None)
        codes = [response.status_code for response in responses]
        assert 503 in codes and 201 in codes  # F1 still produces pool timeouts in the new flow
        assert all(r.json()["error_code"] == "db_pool_timeout" for r in responses if r.status_code == 503)

        states = [(await status(mesh, ref)).json().get("state") for ref in refs]
        held = sum(1 for state in states if state in HOLDS_STOCK)
        assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000 - held
        assert len(mesh["apps"]["payments"].state.repository.rows) == states.count("completed")


@pytest.mark.parametrize(("units", "expected"), [(2, 500), (1, 201)])
async def test_f3_bad_release_signature_in_the_new_flow(orders_db, units, expected):
    async with pg_mesh(orders_db) as mesh:
        runtime = mesh["apps"]["orders"].state.runtime
        await runtime.update(
            RuntimeUpdate(
                expires_at=time.time() + 30, release_version="v2-buggy", persistence_delay_seconds=1
            )
        )
        start = time.perf_counter()
        response = await checkout(mesh, items=(("sku-001", units),))
        assert time.perf_counter() - start >= 1
        assert response.status_code == expected
        if expected == 500:
            assert response.json()["error_code"] == "order_persistence_error"
            assert "OrderPersistenceCompatibilityError" in mesh["logs"].getvalue()
            assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000
        await runtime.update(None)
        assert all(state in TERMINAL_STATES for state in [(await status(mesh)).json()["state"]])


async def test_my_orders_and_details_read_the_durable_checkouts(orders_db):
    async with pg_mesh(orders_db) as mesh:
        legacy = {"product_id": "sku-001", "quantity": 1, "customer_id": "web-user-1"}
        assert (await mesh["client"].post("/api/orders", json=legacy)).status_code == 201
        first = (await checkout(mesh)).json()
        second = (await checkout(mesh, "ATT-0001-0002", items=(("sku-002", 3),))).json()

        response = await mesh["client"].get("/api/orders", params={"customer_id": "web-user-1"})

        assert response.status_code == 200
        listed = response.json()
        assert [row["order_number"] for row in listed] == [second["order_number"], first["order_number"]]
        assert listed[1]["items"] == [
            {"product_id": "sku-001", "quantity": 2, "unit_price_cents": 1200},
            {"product_id": "sku-002", "quantity": 1, "unit_price_cents": 1200},
        ]
        assert [row["created_at"] for row in listed] == [second["created_at"], first["created_at"]]
        assert [(await status(mesh, row["attempt_ref"])).json() for row in listed] == listed
