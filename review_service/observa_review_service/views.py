"""Read-side composition: F7 version records plus the F6 review-status projection.

F6 is optional (the `f6` extra). Without it every version reads as pending_review and
review actions are unavailable; nothing is ever shown as approved by default.
"""
from collections.abc import Callable
from importlib import import_module

from sqlalchemy import Connection

from observa_reports.service import ReportVersion, ReviewStatus, VersionRecord

StatusReader = Callable[[Connection, str, int], ReviewStatus]


def pending_only(conn: Connection, report_id: str, version: int) -> ReviewStatus:
    return "pending_review"


def f6_status_reader() -> StatusReader | None:
    """F6's projection, when the observa-hitl package (the f6 extra) provides it."""
    try:
        return import_module("observa_hitl.projections").review_status
    except (ImportError, AttributeError):
        return None


def default_status_reader() -> StatusReader:
    return f6_status_reader() or pending_only


def report_version(conn: Connection, record: VersionRecord, status_reader: StatusReader) -> ReportVersion:
    return ReportVersion.from_record(record, status_reader(conn, record.report_id, record.version))
