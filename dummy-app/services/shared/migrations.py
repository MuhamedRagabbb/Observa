"""Additive, versioned schema migrations applied by each service's own database role.

Files are named ``NNN_description.sql`` and applied in order, each in its own transaction,
under a session advisory lock so concurrent starters cannot race. Applied files are
checksummed; editing one after it has been applied is an error.

Not wired into service startup yet: apply explicitly after a reviewed backup.
"""

import argparse
import asyncio
import hashlib
from pathlib import Path

import asyncpg

LOCK_KEY = 4_177_301  # arbitrary, stable advisory-lock key for schema changes


class MigrationError(RuntimeError):
    pass


def migration_files(directory):
    return sorted(Path(directory).glob("[0-9][0-9][0-9]_*.sql"))


async def apply_migrations(conn, directory):
    """Apply pending migrations; return the versions applied in this call."""
    await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        applied = {
            row["version"]: row["checksum"] for row in await conn.fetch("SELECT * FROM schema_migrations")
        }
        done = []
        for path in migration_files(directory):
            version, sql = path.stem, path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode()).hexdigest()
            if version in applied:
                if applied[version] != checksum:
                    raise MigrationError(f"Applied migration {version} was modified")
                continue
            try:
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_migrations (version, checksum) VALUES ($1, $2)", version, checksum
                    )
            except asyncpg.PostgresError as exc:
                raise MigrationError(f"Migration {version} failed: {type(exc).__name__}") from None
            done.append(version)
        return done
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)


async def _main(dsn, directory):
    conn = await asyncpg.connect(dsn)
    try:
        for version in await apply_migrations(conn, directory):
            print(f"applied {version}")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apply additive schema migrations for one service database")
    parser.add_argument("--dsn", required=True, help="Service-role DSN, e.g. the service's DATABASE_URL")
    parser.add_argument("--dir", required=True, help="Migration directory, e.g. scripts/migrations/inventory")
    args = parser.parse_args()
    asyncio.run(_main(args.dsn, args.dir))
