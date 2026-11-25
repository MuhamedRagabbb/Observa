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


def sha(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


REVIEWERS = [
    {"reviewer_id": "rev-alice", "display_name": "Alice (SRE)", "roles": ["reviewer", "viewer"],
     "token_sha256": sha("alice-token")},
    {"reviewer_id": "rev-bob", "display_name": "Bob (SRE)", "roles": ["reviewer"], "token_sha256": sha("bob-token")},
    {"reviewer_id": "view-vic", "display_name": "Vic (viewer)", "roles": ["viewer"], "token_sha256": sha("vic-token")},
]
SERVICE_TOKEN = "service-token"


def write_reviewers(path: Path) -> Path:
    path.write_text(json.dumps({"reviewers": REVIEWERS}), encoding="utf-8")
    return path
