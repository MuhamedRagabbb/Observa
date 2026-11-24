"""The seed command loads demo reports so the dashboard can be viewed locally."""
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
