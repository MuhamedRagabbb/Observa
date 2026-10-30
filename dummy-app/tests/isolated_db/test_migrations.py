import asyncio
import time
from pathlib import Path

import asyncpg
import pytest

from services.shared.migrations import MigrationError, apply_migrations

INVENTORY = Path(__file__).resolve().parents[2] / "scripts" / "migrations" / "inventory"


async def test_inventory_migrations_apply_once_and_preserve_existing_data(inventory_db):
    conn = await inventory_db["connect"]()
    try:
        await conn.execute("INSERT INTO reservations VALUES ('order-legacy', 'sku-001', 2, 'reserved')")
        before = await conn.fetch("SELECT product_id, stock FROM products ORDER BY product_id")

        assert await apply_migrations(conn, INVENTORY) == ["001_cart_reservations"]
        assert await apply_migrations(conn, INVENTORY) == []

        assert await conn.fetch("SELECT product_id, stock FROM products ORDER BY product_id") == before
        assert await conn.fetchval("SELECT status FROM reservations WHERE reservation_id='order-legacy'") == (
            "reserved"
        )
        tables = {row["tablename"] for row in await conn.fetch("SELECT tablename FROM pg_tables")}
        assert {"cart_reservations", "cart_reservation_lines", "schema_migrations"} <= tables
    finally:
        await conn.close()


async def test_edited_migration_is_rejected(inventory_db, tmp_path):
    (tmp_path / "001_example.sql").write_text("CREATE TABLE example_a (id int);")
    conn = await inventory_db["connect"]()
    try:
        assert await apply_migrations(conn, tmp_path) == ["001_example"]
        (tmp_path / "001_example.sql").write_text("CREATE TABLE example_b (id int);")
        with pytest.raises(MigrationError, match="001_example"):
            await apply_migrations(conn, tmp_path)
    finally:
        await conn.close()


async def test_failed_migration_rolls_back_and_is_not_recorded(inventory_db, tmp_path):
    (tmp_path / "001_ok.sql").write_text("CREATE TABLE example_ok (id int);")
    (tmp_path / "002_broken.sql").write_text(
        "CREATE TABLE example_half (id int); SELECT missing_column FROM x;"
    )
    conn = await inventory_db["connect"]()
    try:
        with pytest.raises(MigrationError, match="002_broken"):
            await apply_migrations(conn, tmp_path)
        tables = {row["tablename"] for row in await conn.fetch("SELECT tablename FROM pg_tables")}
        assert "example_ok" in tables and "example_half" not in tables
        versions = await conn.fetch("SELECT version FROM schema_migrations ORDER BY version")
        assert [row["version"] for row in versions] == ["001_ok"]
    finally:
        await conn.close()


async def test_checksum_ignores_line_endings(inventory_db, tmp_path):
    path = tmp_path / "001_example.sql"
    path.write_bytes(b"CREATE TABLE example_eol (id int);\nSELECT 1;\n")
    conn = await inventory_db["connect"]()
    try:
        assert await apply_migrations(conn, tmp_path) == ["001_example"]
        path.write_bytes(b"CREATE TABLE example_eol (id int);\r\nSELECT 1;\r\n")
        assert await apply_migrations(conn, tmp_path) == []
    finally:
        await conn.close()


ROOT_MIGRATIONS = Path(__file__).resolve().parents[2] / "scripts" / "migrations"


async def test_orders_migration_adds_attempt_tables_and_keeps_legacy_orders(orders_db):
    conn = await orders_db["connect"]()
    try:
        await conn.execute(
            "INSERT INTO orders (order_id, product_id, quantity, customer_id, status) "
            "VALUES ('order-legacy', 'sku-001', 1, 'legacy', 'pending')"
        )
        assert await apply_migrations(conn, ROOT_MIGRATIONS / "orders") == [
            "001_checkout_attempts",
            "002_checkout_pricing",
            "003_reconciler",
        ]
        assert await apply_migrations(conn, ROOT_MIGRATIONS / "orders") == []
        assert await conn.fetchval("SELECT status FROM orders WHERE order_id='order-legacy'") == "pending"
        tables = {row["tablename"] for row in await conn.fetch("SELECT tablename FROM pg_tables")}
        assert {"checkout_attempts", "order_items"} <= tables
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO checkout_attempts (customer_id, attempt_ref, state) VALUES ('c', 'bad-ref', 'received')"
            )
    finally:
        await conn.close()


async def test_payments_migration_allows_void_tombstones_only(payments_db):
    conn = await payments_db["connect"]()
    try:
        await conn.execute(
            "INSERT INTO payments (payment_id, idempotency_key, order_id, customer_id, amount_cents, status) "
            "VALUES ('payment-legacy', 'k-legacy', 'order-legacy', 'legacy', 1200, 'succeeded')"
        )
        assert await apply_migrations(conn, ROOT_MIGRATIONS / "payments") == ["001_payment_void_fence"]
        await conn.execute(
            "INSERT INTO payments (payment_id, idempotency_key, order_id, status) "
            "VALUES ('void-1', 'k-void', 'order-void', 'voided')"
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO payments (payment_id, idempotency_key, order_id, status) "
                "VALUES ('charge-1', 'k-charge', 'order-charge', 'succeeded')"
            )
        assert (
            await conn.fetchval("SELECT amount_cents FROM payments WHERE payment_id='payment-legacy'") == 1200
        )
    finally:
        await conn.close()


async def test_lock_timeout_in_the_dsn_bounds_a_blocked_migration(payments_db):
    """The runbook appends ?lock_timeout=... to the service DSN: a blocked step fails fast,
    rolls back and is not recorded, instead of queueing live traffic behind it."""

    blocker = await payments_db["connect"]()
    migrator = await asyncpg.connect(payments_db["dsn"] + "?lock_timeout=1s")
    try:
        async with blocker.transaction():
            await blocker.execute("LOCK TABLE payments IN ACCESS SHARE MODE")  # a long-running reader
            started = time.monotonic()
            with pytest.raises(MigrationError, match="001_payment_void_fence"):
                await asyncio.wait_for(apply_migrations(migrator, ROOT_MIGRATIONS / "payments"), 10)
            assert time.monotonic() - started < 5
        assert await migrator.fetchval("SELECT count(*) FROM schema_migrations") == 0
        assert not await migrator.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='payments_charge_fields')"
        )
    finally:
        await migrator.close()
        await blocker.close()


def rollback_scripts(service):
    """The runbook's rollback-before-data scripts, newest first."""
    return sorted((ROOT_MIGRATIONS / service / "rollback").glob("[0-9][0-9][0-9]_*.sql"), reverse=True)


async def schema(conn):
    """Tables, columns, constraints and indexes, ignoring the runner's own schema_migrations."""
    tables = {
        r["tablename"] for r in await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    columns = {
        (r["table_name"], r["column_name"], r["is_nullable"])
        for r in await conn.fetch(
            "SELECT table_name, column_name, is_nullable FROM information_schema.columns WHERE table_schema='public'"
        )
    }
    constraints = {
        (r["table"], r["conname"], r["def"])
        for r in await conn.fetch(
            "SELECT conrelid::regclass::text AS table, conname, pg_get_constraintdef(oid) AS def "
            "FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
        )
    }
    indexes = {
        (r["tablename"], r["indexname"])
        for r in await conn.fetch("SELECT tablename, indexname FROM pg_indexes WHERE schemaname='public'")
    }
    sequences = {r["relname"] for r in await conn.fetch("SELECT relname FROM pg_class WHERE relkind='S'")}
    bookkeeping = "schema_migrations"
    return (
        tables - {bookkeeping},
        {c for c in columns if c[0] != bookkeeping},
        {c for c in constraints if c[0] != bookkeeping},
        {i for i in indexes if i[0] != bookkeeping},
        sequences,
    )


@pytest.fixture(params=["inventory", "orders", "payments"])
def service_db(request):
    """(service, fresh database). Sync on purpose: database fixtures start outside the test loop."""
    return request.param, request.getfixturevalue(f"{request.param}_db")


async def test_rollback_before_data_restores_the_original_schema(service_db):
    service, database = service_db
    conn = await database["connect"]()
    try:
        original = await schema(conn)
        applied = await apply_migrations(conn, ROOT_MIGRATIONS / service)
        assert [path.stem for path in rollback_scripts(service)] == applied[::-1]
        for path in rollback_scripts(service):
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
        assert await schema(conn) == original
        assert await conn.fetchval("SELECT count(*) FROM schema_migrations") == 0
        assert await apply_migrations(conn, ROOT_MIGRATIONS / service) == applied  # can re-apply later
    finally:
        await conn.close()


NEW_DATA = {
    "inventory": "INSERT INTO cart_reservations (reservation_id, status) VALUES ('order-x', 'released')",
    "orders": "INSERT INTO checkout_attempts (customer_id, attempt_ref, state) VALUES ('c', 'ATT-0000-0001', 'abandoned')",
    "payments": "INSERT INTO payments (payment_id, idempotency_key, order_id, status) VALUES ('v', 'k', 'o', 'voided')",
}


async def test_rollback_refuses_once_new_checkout_data_exists(service_db):
    service, database = service_db
    conn = await database["connect"]()
    try:
        applied = await apply_migrations(conn, ROOT_MIGRATIONS / service)
        await conn.execute(NEW_DATA[service])
        with pytest.raises(asyncpg.RaiseError, match="recovery"):
            async with conn.transaction():
                await conn.execute(rollback_scripts(service)[0].read_text(encoding="utf-8"))
        assert [
            r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations ORDER BY 1")
        ] == applied
    finally:
        await conn.close()
