"""F6 test fixtures: a migrated SQLite file database (the composition root's migration and
engine, so the immutability triggers are real), published F7 reports and reviewers."""
import pytest
from sqlalchemy import text

from observa_hitl.auth import ReviewerDirectory
from observa_hitl.gate import ReviewGate
from observa_reports.service import ReportService, RevisionRef
from observa_review_service.db import make_engine, unit_of_work, upgrade

from hitl_helpers import Clock, bundle, write_reviewers


@pytest.fixture()
def db_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'hitl.sqlite3').as_posix()}"
    upgrade(url)
    return url


@pytest.fixture()
def engine(db_url):
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def uow(engine):
    return lambda: unit_of_work(engine)


@pytest.fixture()
def gate(clock):
    return ReviewGate(clock=clock)


@pytest.fixture()
def directory(tmp_path):
    return ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json"))


@pytest.fixture()
def alice(directory):
    return directory.get("rev-alice")


@pytest.fixture()
def bob(directory):
    return directory.get("rev-bob")


@pytest.fixture()
def vic(directory):
    return directory.get("view-vic")


@pytest.fixture()
def publish(engine, clock, gate):
    """Publish a fixture bundle (as F7 does) and open its review cycle (as the composition root does)."""
    def _publish(name, expected_previous=None, review_cycle_id=None):
        revision = RevisionRef(review_cycle_id=review_cycle_id, expected_previous=expected_previous) \
            if expected_previous else None
        with unit_of_work(engine) as conn:
            record = ReportService(clock=clock).publish(conn, bundle(name), revision_of=revision)
            gate.open(conn, record.report_id, record.version)
        return record
    return _publish


@pytest.fixture()
def count(engine):
    def _count(table, where="1=1", **params):
        with engine.connect() as conn:
            return conn.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params).scalar_one()
    return _count
