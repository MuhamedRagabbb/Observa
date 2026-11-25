"""Helpers shared by F7 tests (a uniquely named module, so it never collides with another conftest)."""
import json
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event

from observa_reports.inputs import ReportInputs

FIXTURES = Path(__file__).resolve().parents[2] / "review_service" / "fixtures"


def load(group, name):
    return json.loads((FIXTURES / group / f"{name}.json").read_text(encoding="utf-8"))


def bundle(name) -> ReportInputs:
    return ReportInputs.model_validate(load("bundles", name))


def sqlite_engine(path: Path):
    """Same SQLite settings as the review service: WAL, foreign keys, BEGIN IMMEDIATE."""
    engine = create_engine(f"sqlite:///{path.as_posix()}")

    @event.listens_for(engine, "connect")
    def _configure(dbapi_connection, _record):
        dbapi_connection.isolation_level = None
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=10000")
        cursor.close()

    @event.listens_for(engine, "begin")
    def _begin_immediate(conn):
        conn.exec_driver_sql("BEGIN IMMEDIATE")

    return engine


@contextmanager
def uow(engine):
    with engine.begin() as conn:
        yield conn
