"""The seed command loads demo reports so the dashboard can be viewed locally."""
from importlib.util import find_spec

import pytest
from sqlalchemy import text

from observa_review_service.db import make_engine
from observa_review_service.seed import main


def counts(url):
    engine = make_engine(url)
    try:
        with engine.connect() as conn:
            return {row[0]: row[1] for row in conn.execute(text(
                "SELECT incident_id, current_version FROM report_heads"))}
    finally:
        engine.dispose()


def test_seed_publishes_demo_reports_and_is_idempotent(tmp_path):
    url = f"sqlite:///{(tmp_path / 'data' / 'review.sqlite3').as_posix()}"
    main(["--database-url", url])
    first = counts(url)
    main(["--database-url", url])
    assert counts(url) == first
    assert len(first) == 3 and first["INC-6558d5b7036a44934e9e"] == 2  # payments v1 then its revision


@pytest.mark.skipif(find_spec("observa_hitl") is None, reason="needs the f6 extra")
def test_seed_opens_a_review_cycle_for_every_version_when_f6_is_installed(tmp_path):
    url = f"sqlite:///{(tmp_path / 'data' / 'review.sqlite3').as_posix()}"
    main(["--database-url", url])
    main(["--database-url", url])
    engine = make_engine(url)
    try:
        with engine.connect() as conn:
            cycles = conn.execute(text("SELECT report_id, report_version, state FROM review_cycles")).all()
            versions = conn.execute(text("SELECT report_id, version FROM report_versions")).all()
    finally:
        engine.dispose()
    assert sorted((r, v) for r, v, _ in cycles) == sorted(tuple(v) for v in versions)
    by_version = {(r, v): s for r, v, s in cycles}
    payments = [k for k in by_version if k[1] == 2][0][0]
    assert by_version[(payments, 1)] == "superseded"  # v2 was published on top of it
    assert {s for k, s in by_version.items() if k != (payments, 1)} == {"pending_review"}
