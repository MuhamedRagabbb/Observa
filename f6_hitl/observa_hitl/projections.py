"""Read-side projections from F6's durable records. Nothing here is stored in report content."""
from sqlalchemy import Connection, select

from .contracts import MemorySyncStatus, ReviewStatus
from .ports import MEMORY_EVENT
from .tables import outbox_events, review_decisions

_SYNC = {"pending": "pending", "delivered": "stored", "failed": "failed"}


def review_status(conn: Connection, report_id: str, version: int) -> ReviewStatus:
    decisions = set(conn.execute(select(review_decisions.c.decision).where(
        review_decisions.c.report_id == report_id, review_decisions.c.version == version)).scalars())
    if "approve" in decisions:
        return "approved"
    if "request_changes" in decisions:
        return "changes_requested"
    return "pending_review"


def memory_sync_status(conn: Connection, decision_id: str) -> MemorySyncStatus:
    status = conn.execute(select(outbox_events.c.status).where(
        outbox_events.c.event_type == MEMORY_EVENT, outbox_events.c.dedupe_key == decision_id)).scalar_one_or_none()
    return _SYNC.get(status, "not_requested")
