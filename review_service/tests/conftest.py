"""Shared fixtures for the composition root: a migrated database, published reports and an app."""
from importlib.util import find_spec

import pytest

from observa_reports.service import ReportService, RevisionRef
from observa_review_service.db import make_engine, unit_of_work, upgrade

from review_helpers import CLOCK, bundle

HAS_F6 = find_spec("observa_hitl") is not None
# The F7-only install (no f6 extra) must still pass: F6-dependent modules are not collected there.
collect_ignore = [] if HAS_F6 else ["api/test_auth_api.py", "api/test_decisions_api.py", "api/test_internal_api.py",
                                    "ui/test_review_states.py", "test_worker.py"]


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


@pytest.fixture()
def client_f7_only(engine):
    """The read side exactly as it runs without the optional F6 package."""
    from fastapi.testclient import TestClient
    from observa_review_service.app import create_app
    with TestClient(create_app(engine=engine, f6=False)) as test_client:
        yield test_client
