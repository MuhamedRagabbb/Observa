"""Engine construction, the unit of work and schema migration.

F6 and F7 code never opens transactions: callers take a Connection from
unit_of_work(), so an approval, the report-head check and the outbox row
commit or roll back together.
"""
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


def make_engine(url: str) -> Engine:
    engine = create_engine(url)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _configure(dbapi_connection, _record):
            # Let SQLAlchemy's "begin" event, not the driver, start transactions.
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=10000")
            cursor.close()

        @event.listens_for(engine, "begin")
        def _begin_immediate(conn):
            # Take the write lock at BEGIN so check-then-write sequences cannot interleave.
            conn.exec_driver_sql("BEGIN IMMEDIATE")
    return engine


@contextmanager
def unit_of_work(engine: Engine) -> Iterator[Connection]:
    with engine.begin() as conn:
        yield conn


def upgrade(url: str, revision: str = "head") -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, revision)
