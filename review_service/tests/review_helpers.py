"""Helpers shared by review_service tests (uniquely named so it never collides with F7's helpers)."""
import json
from datetime import datetime, timezone
from pathlib import Path

from observa_reports.inputs import ReportInputs

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CLOCK = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def load(group, name):
    return json.loads((FIXTURES / group / f"{name}.json").read_text(encoding="utf-8"))


def bundle(name) -> ReportInputs:
    return ReportInputs.model_validate(load("bundles", name))
