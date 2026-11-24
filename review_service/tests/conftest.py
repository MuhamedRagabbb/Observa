"""Shared fixtures for the composition root: a migrated database, published reports and an app."""
import pytest

from observa_reports.service import ReportService, RevisionRef
from observa_review_service.db import make_engine, unit_of_work, upgrade

from review_helpers import CLOCK, bundle


@pytest.fixture()
def db_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'review.sqlite3').as_posix()}"
    upgrade(url)
    return url


@pytest.fixture()
def engine(db_url):
    eng = make_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture()
def publish(engine):
    def _publish(inputs, expected_previous=None, review_cycle_id=None):
        inputs = bundle(inputs) if isinstance(inputs, str) else inputs
        revision = RevisionRef(review_cycle_id=review_cycle_id, expected_previous=expected_previous) \
            if expected_previous else None
        with unit_of_work(engine) as conn:
            return ReportService(clock=lambda: CLOCK).publish(conn, inputs, revision_of=revision)
    return _publish


@pytest.fixture()
def client(engine):
    from fastapi.testclient import TestClient
    from observa_review_service.app import create_app
    with TestClient(create_app(engine=engine)) as test_client:
        yield test_client
