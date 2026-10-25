from pathlib import Path

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
    import asyncpg

    conn = await orders_db["connect"]()
    try:
        await conn.execute(
            "INSERT INTO orders (order_id, product_id, quantity, customer_id, status) "
            "VALUES ('order-legacy', 'sku-001', 1, 'legacy', 'pending')"
        )
        assert await apply_migrations(conn, ROOT_MIGRATIONS / "orders") == ["001_checkout_attempts"]
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
    import asyncpg

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
