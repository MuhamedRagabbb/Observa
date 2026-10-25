"""Throwaway PostgreSQL for schema and concurrency tests. Never touches the Compose database.

The container uses tmpfs storage, a random loopback port and is removed after the session.
It is initialised with the same scripts/init-db.* as the real stack.
"""

import asyncio
import os
import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = ROOT / "scripts" / "migrations"
IMAGE = "postgres:16.6-bookworm"
PASSWORDS = {
    "postgres": "iso-admin",
    "orders_app": "iso-orders",
    "inventory_app": "iso-inventory",
    "payments_app": "iso-payments",
}

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
        "-e", f"POSTGRES_PASSWORD={PASSWORDS['postgres']}",
        "-e", f"ORDERS_DB_PASSWORD={PASSWORDS['orders_app']}",
        "-e", f"INVENTORY_DB_PASSWORD={PASSWORDS['inventory_app']}",
        "-e", f"PAYMENTS_DB_PASSWORD={PASSWORDS['payments_app']}",
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
            conn = await connect(port, "payments_db", "payments_app")
            await conn.fetchval("SELECT count(*) FROM payments")
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


@asynccontextmanager
async def fresh_database(port, service):
    """A fresh copy of one base (unmigrated) service database, owned by its service role."""
    template, role, name = f"{service}_db", f"{service}_app", f"{service}_{uuid.uuid4().hex[:10]}"
    admin = await connect(port, "postgres", "postgres")
    await admin.execute(f'CREATE DATABASE "{name}" TEMPLATE {template} OWNER {role}')
    await admin.close()
    try:
        yield {
            "port": port,
            "name": name,
            "service": service,
            "dsn": f"postgresql://{role}:{PASSWORDS[role]}@127.0.0.1:{port}/{name}",
            "connect": lambda: connect(port, name, role),
        }
    finally:
        admin = await connect(port, "postgres", "postgres")
        await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()


@asynccontextmanager
async def open_repository(database, repository_class, *, migrate):
    """The real repository over a real Database pool; reopen it to simulate a service restart."""
    from services.shared.config import Settings
    from services.shared.database import Database
    from services.shared.migrations import apply_migrations
    from services.shared.runtime import Runtime
    from services.shared.telemetry import Telemetry

    if migrate:
        conn = await database["connect"]()
        await apply_migrations(conn, MIGRATIONS / database["service"])
        await conn.close()
    settings = Settings(dsn=database["dsn"], otlp_endpoint="", pool_max=8, pool_timeout=5)
    telemetry = Telemetry(f"{database['service']}-test", settings)
    db = Database(settings, telemetry, Runtime(telemetry))
    await db.open()
    try:
        yield repository_class(db)
    finally:
        await db.close()
        telemetry.close()


@pytest.fixture
async def inventory_db(postgres_port):
    async with fresh_database(postgres_port, "inventory") as database:
        yield database


@pytest.fixture
async def inventory(inventory_db):
    """The real InventoryRepository over a migrated isolated database."""
    from services.shared.repositories import InventoryRepository

    async with open_repository(inventory_db, InventoryRepository, migrate=True) as repository:
        yield repository


@pytest.fixture
async def unmigrated_inventory(inventory_db):
    """The real InventoryRepository over the base schema, like the current live database."""
    from services.shared.repositories import InventoryRepository

    async with open_repository(inventory_db, InventoryRepository, migrate=False) as repository:
        yield repository


@pytest.fixture
async def orders_db(postgres_port):
    async with fresh_database(postgres_port, "orders") as database:
        yield database


@pytest.fixture
async def payments_db(postgres_port):
    async with fresh_database(postgres_port, "payments") as database:
        yield database
