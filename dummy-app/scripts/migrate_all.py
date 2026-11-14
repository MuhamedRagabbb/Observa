"""One-shot Compose database setup; no service accepts work before this succeeds."""
import asyncio
import os
from pathlib import Path

import asyncpg

from services.shared.migrations import apply_migrations, migration_files

SERVICES = ("inventory", "payments", "orders")
MIGRATIONS = Path(__file__).resolve().parent / "migrations"


async def migrate_databases(env=None, directory=MIGRATIONS):
    env = os.environ if env is None else env
    # Check all inputs before changing any database. Do not print credentials.
    for service in SERVICES:
        if not env.get(service.upper() + "_DATABASE_URL"):
            raise RuntimeError(f"Missing database configuration for {service}")
        if not migration_files(directory / service):
            raise RuntimeError(f"Missing migration files for {service}")
    for service in SERVICES:
        connection = None
        try:
            connection = await asyncpg.connect(
                env[service.upper() + "_DATABASE_URL"],
                timeout=10,
                command_timeout=120,
                server_settings={"lock_timeout": "5s", "statement_timeout": "120s"},
            )
            applied = await apply_migrations(connection, directory / service)
        except Exception as exc:
            raise RuntimeError(
                f"Database setup failed for {service} ({type(exc).__name__}); "
                "application startup is blocked."
            ) from None
        finally:
            if connection is not None:
                await connection.close(timeout=5)
        message = ", ".join(applied) if applied else "already up to date"
        print(f"{service}: {message}", flush=True)
    print("Database setup completed successfully.", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(migrate_databases())
    except Exception as exc:
        raise SystemExit(str(exc)) from None
