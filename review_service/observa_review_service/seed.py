"""Load demo report versions from the fixture bundles so the dashboard can be viewed locally.

    python -m observa_review_service.seed [--database-url URL] [--bundles NAME ...]

A bundle for an incident that already has a report is published as the next version on
top of the current one; republishing identical inputs changes nothing.
"""
import argparse
import json
from pathlib import Path

from observa_reports.errors import ReportError
from observa_reports.inputs import ReportInputs
from observa_reports.service import ReportService, RevisionRef

from .db import make_engine, unit_of_work, upgrade
from .settings import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "bundles"
DEMO = ["payments_v1", "payments_v2_revised", "db_exhaustion", "bad_deployment"]


def _sqlite_parent(url: str) -> None:
    if url.startswith("sqlite:///") and ":memory:" not in url:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--database-url", default=Settings().DATABASE_URL)
    ap.add_argument("--bundles", nargs="+", default=DEMO, help=f"bundle names under {FIXTURES}")
    args = ap.parse_args(argv)
    _sqlite_parent(args.database_url)
    upgrade(args.database_url)
    engine, service = make_engine(args.database_url), ReportService()
    try:
        for name in args.bundles:
            inputs = ReportInputs.model_validate(json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")))
            with unit_of_work(engine) as conn:
                try:
                    record = service.publish(conn, inputs)
                except ReportError as exc:
                    current = (exc.envelope.details or {}).get("current_version")
                    if exc.envelope.code != "VERSION_CONFLICT" or current is None:
                        raise
                    record = service.publish(conn, inputs, revision_of=RevisionRef(
                        review_cycle_id=None, expected_previous=current))
            print(f"{name}: {record.report_id} v{record.version} ({record.incident_id})")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
