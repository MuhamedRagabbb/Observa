"""Throwaway PostgreSQL for schema and concurrency tests. Never touches the Compose database.

The container uses tmpfs storage, a random loopback port and is removed after the session.
It is initialised with the same scripts/init-db.* as the real stack.
"""

import asyncio
import os
import subprocess
import uuid
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "postgres:16.6-bookworm"
PASSWORDS = {"postgres": "iso-admin", "inventory_app": "iso-inventory"}

pytestmark = pytest.mark.skipif(os.getenv("RUN_DB_TESTS") != "1", reason="Set RUN_DB_TESTS=1 (uses Docker)")


def docker(*args):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="session")
def postgres_port():
    if os.getenv("RUN_DB_TESTS") != "1":
        pytest.skip("Set RUN_DB_TESTS=1 (uses Docker)")
    name = f"observa-isolated-test-{uuid.uuid4().hex[:8]}"
    docker(
        "run", "-d", "--rm", "--name", name, "--tmpfs", "/var/lib/postgresql/data", "-p", "127.0.0.1::5432",
        "-e", f"POSTGRES_PASSWORD={PASSWORDS['postgres']}", "-e", "ORDERS_DB_PASSWORD=iso-orders",
        "-e", f"INVENTORY_DB_PASSWORD={PASSWORDS['inventory_app']}", "-e", "PAYMENTS_DB_PASSWORD=iso-payments",
        "-v", f"{ROOT / 'scripts' / 'init-db.sh'}:/docker-entrypoint-initdb.d/01-init-db.sh:ro",
        "-v", f"{ROOT / 'scripts' / 'init-db.sql'}:/opt/init-db.sql:ro",
        IMAGE,
    )  # fmt: skip
    try:
        port = int(docker("port", name, "5432/tcp").splitlines()[0].rsplit(":", 1)[1])
        asyncio.run(wait_ready(port))
        yield port
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)


async def wait_ready(port):
    # The entrypoint restarts the server after init scripts, so require two consecutive successes.
    successes = 0
    for _ in range(120):
        try:
            conn = await connect(port, "inventory_db", "inventory_app")
            await conn.fetchval("SELECT count(*) FROM products")
            await conn.close()
            successes += 1
            if successes == 2:
                return
        except (OSError, asyncpg.PostgresError):
            successes = 0
        await asyncio.sleep(0.5)
    raise RuntimeError("Isolated PostgreSQL did not become ready")


def connect(port, database, user):
    return asyncpg.connect(
        host="127.0.0.1", port=port, user=user, password=PASSWORDS[user], database=database
    )


@pytest.fixture
async def inventory_db(postgres_port):
    """A fresh copy of the base (unmigrated) inventory database, owned by inventory_app."""
    name = f"inv_{uuid.uuid4().hex[:10]}"
    admin = await connect(postgres_port, "postgres", "postgres")
    await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE inventory_db OWNER inventory_app')
    await admin.close()
    yield {
        "port": postgres_port,
        "name": name,
        "dsn": f"postgresql://inventory_app:{PASSWORDS['inventory_app']}@127.0.0.1:{postgres_port}/{name}",
        "connect": lambda: connect(postgres_port, name, "inventory_app"),
    }
    admin = await connect(postgres_port, "postgres", "postgres")
    await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
    await admin.close()


@pytest.fixture
async def inventory(inventory_db):
    """The real InventoryRepository over a migrated isolated database."""
    async for repository in _repository(inventory_db, migrate=True):
        yield repository


@pytest.fixture
async def unmigrated_inventory(inventory_db):
    """The real InventoryRepository over the base schema, like the current live database."""
    async for repository in _repository(inventory_db, migrate=False):
        yield repository


async def _repository(inventory_db, migrate):
    from services.shared.config import Settings
    from services.shared.database import Database
    from services.shared.migrations import apply_migrations
    from services.shared.repositories import InventoryRepository
    from services.shared.runtime import Runtime
    from services.shared.telemetry import Telemetry

    if migrate:
        conn = await inventory_db["connect"]()
        await apply_migrations(conn, ROOT / "scripts" / "migrations" / "inventory")
        await conn.close()
    settings = Settings(dsn=inventory_db["dsn"], otlp_endpoint="", pool_max=8, pool_timeout=5)
    telemetry = Telemetry("inventory-test", settings)
    db = Database(settings, telemetry, Runtime(telemetry))
    await db.open()
    yield InventoryRepository(db)
    await db.close()
    telemetry.close()
