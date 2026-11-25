"""Helpers shared by the F6 tests (uniquely named so it never collides with other suites' helpers)."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from observa_reports.inputs import ReportInputs

FIXTURES = Path(__file__).resolve().parents[2] / "review_service" / "fixtures"
CLOCK = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)


def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


REVIEWERS = [
    {"reviewer_id": "rev-alice", "display_name": "Alice (SRE)", "roles": ["reviewer", "viewer"],
     "token_sha256": sha("alice-token")},
    {"reviewer_id": "rev-bob", "display_name": "Bob (SRE)", "roles": ["reviewer"], "token_sha256": sha("bob-token")},
    {"reviewer_id": "view-vic", "display_name": "Vic (viewer)", "roles": ["viewer"], "token_sha256": sha("vic-token")},
]


def write_reviewers(path: Path) -> Path:
    path.write_text(json.dumps({"reviewers": REVIEWERS}), encoding="utf-8")
    return path


def bundle(name: str) -> ReportInputs:
    return ReportInputs.model_validate(json.loads((FIXTURES / "bundles" / f"{name}.json").read_text(encoding="utf-8")))


class Clock:
    """A settable UTC clock for deterministic timestamps and backoff checks."""

    def __init__(self, now: datetime = CLOCK):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)
