"""F7 report versions: immutable, content-addressed, advanced only by compare-and-swap (F7-US-03).

The caller owns the transaction (pass a Connection from the unit of work), so a
publish is all-or-nothing: content, version row and head pointer commit together,
and a failure at any step leaves the previous version current.
"""
import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Connection, and_, insert, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .assemble import assemble
from .content import SCHEMA_VERSION, ReportContent, canonical_json
from .errors import ReportError
from .inputs import ReportInputs, UtcDatetime
from .tables import report_contents, report_heads, report_versions


def report_id_for(incident_id: str) -> str:
    """One stable report per incident (contract invariant 1)."""
    return "RPT-" + hashlib.sha256(incident_id.encode()).hexdigest()[:16]


class RevisionRef(BaseModel):
    """Asks for vN+1 on top of `expected_previous`. review_cycle_id is the F6 cycle that requested
    the revision, or None for an upstream update (e.g. the final assessment after recovery)."""
    model_config = ConfigDict(frozen=True)
    review_cycle_id: str | None
    expected_previous: int = Field(ge=1)


class VersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)
    report_id: str
    incident_id: str
    version: int
    previous_version: int | None
    content_ref: str
    content_digest: str
    source_investigation_run_id: str
    source_correlation_run_id: str | None
    created_at: UtcDatetime
    review_cycle_id: str | None
    evidence_ids: list[str]
    uncertainty_flags: list[str]


class StoredReport(BaseModel):
    model_config = ConfigDict(frozen=True)
    record: VersionRecord
    content: ReportContent


ReviewStatus = Literal["pending_review", "changes_requested", "approved"]


class ReportVersion(BaseModel):
    """Contract `ReportVersion` (F7 -> F6). review_status is a read-only projection of F6
    decisions; it is never stored inside the immutable content."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    report_id: str
    incident_id: str
    version: int
    previous_version: int | None
    content_ref: str
    content_digest: str
    source_investigation_run_id: str
    created_at: UtcDatetime
    review_status: ReviewStatus
    review_cycle_id: str | None
    evidence_ids: list[str]
    uncertainty_flags: list[str]

    @classmethod
    def from_record(cls, record: VersionRecord, review_status: ReviewStatus) -> "ReportVersion":
        return cls(review_status=review_status, **record.model_dump(exclude={"source_correlation_run_id"}))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _uncertainty_flags(content: ReportContent) -> list[str]:
    flags = set()
    if content.root_cause.status != "leading":
        flags.add(f"root_cause_{content.root_cause.status}")
    if content.recommendations.status != "ranked":
        flags.add(content.recommendations.status if content.recommendations.status == "no_validated_remediation"
                  else "recommendations_unavailable")
    if content.missing_evidence:
        flags.add("missing_evidence")
    if any(e.reference_status != "valid" for e in content.evidence_register):
        flags.add("unresolved_evidence")
    if any(a.relation == "contradicts" for e in content.evidence_register for a in e.assessments):
        flags.add("contradicting_evidence")
    if any(getattr(content.findings, s).status not in ("completed",) for s in ("logs", "metrics", "traces")):
        flags.add("incomplete_signals")
    if content.alerts.missing_alert_ids:
        flags.add("missing_alert_records")
    return sorted(flags)


def _is_unique_violation(exc: IntegrityError) -> bool:
    orig = exc.orig
    return (getattr(orig, "sqlite_errorname", "") in ("SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY")
            or getattr(orig, "pgcode", None) == "23505")


def _record(row) -> VersionRecord:
    return VersionRecord(
        report_id=row.report_id, incident_id=row.incident_id, version=row.version,
        previous_version=row.previous_version, content_ref=f"sha256:{row.content_digest}",
        content_digest=row.content_digest, source_investigation_run_id=row.source_investigation_run_id,
        source_correlation_run_id=row.source_correlation_run_id, created_at=row.created_at,
        review_cycle_id=row.revision_of_cycle_id, evidence_ids=json.loads(row.evidence_ids),
        uncertainty_flags=json.loads(row.uncertainty_flags))


_VERSION_COLUMNS = (report_versions.c.report_id, report_heads.c.incident_id, report_versions.c.version,
                    report_versions.c.previous_version, report_versions.c.content_digest,
                    report_versions.c.source_investigation_run_id, report_versions.c.source_correlation_run_id,
                    report_versions.c.created_at, report_versions.c.revision_of_cycle_id,
                    report_versions.c.evidence_ids, report_versions.c.uncertainty_flags)


def _versions():
    return select(*_VERSION_COLUMNS).join(report_heads, report_heads.c.report_id == report_versions.c.report_id)


class ReportService:
    def __init__(self, clock: Callable[[], datetime] = _now):
        self._clock = clock

    # ---- write ------------------------------------------------------------
    def publish(self, conn: Connection, inputs: ReportInputs, *,
                revision_of: RevisionRef | None = None) -> VersionRecord:
        incident_id = inputs.incident.incident_id
        report_id = report_id_for(incident_id)
        op = f"publish:{report_id}"
        if inputs.investigation is None:
            raise ReportError("VALIDATION_ERROR", "a report version needs the F2 investigation run it was built from",
                              operation_id=op)
        content = assemble(inputs)
        body = canonical_json(content)
        digest = hashlib.sha256(body).hexdigest()
        run_id = inputs.investigation.run_id
        try:
            existing = conn.execute(_versions().where(and_(
                report_versions.c.report_id == report_id,
                report_versions.c.source_investigation_run_id == run_id,
                report_versions.c.content_digest == digest))).first()
            if existing is not None:
                return _record(existing)  # same run and same content: idempotent republish

            head = conn.execute(select(report_heads.c.current_version)
                                .where(report_heads.c.report_id == report_id)).scalar()
            if head is None and revision_of is not None:
                raise ReportError("VERSION_CONFLICT", f"cannot revise {report_id}: it has no versions yet",
                                  operation_id=op)
            if head is not None and revision_of is None:
                raise ReportError("VERSION_CONFLICT", f"{report_id} already has v{head}; publish a revision with "
                                  f"expected_previous={head}", operation_id=op, details={"current_version": head})
            if head is not None and revision_of.expected_previous != head:
                raise ReportError("VERSION_CONFLICT", f"expected_previous={revision_of.expected_previous} but "
                                  f"the current version is {head}", operation_id=op,
                                  details={"current_version": head})
            version, previous = (1, None) if head is None else (head + 1, head)
            now = _iso(self._clock())

            if conn.execute(select(report_contents.c.content_digest)
                            .where(report_contents.c.content_digest == digest)).first() is None:
                conn.execute(insert(report_contents).values(content_digest=digest, schema_version=SCHEMA_VERSION,
                                                            body_json=body.decode("utf-8"), created_at=now))
            if head is None:
                conn.execute(insert(report_heads).values(report_id=report_id, incident_id=incident_id,
                                                         current_version=1, created_at=now, updated_at=now))
            conn.execute(insert(report_versions).values(
                report_id=report_id, version=version, previous_version=previous, content_digest=digest,
                source_investigation_run_id=run_id,
                source_correlation_run_id=inputs.correlation.run_id if inputs.correlation else None,
                revision_of_cycle_id=revision_of.review_cycle_id if revision_of else None,
                evidence_ids=json.dumps([e.evidence_id for e in content.evidence_register]),
                uncertainty_flags=json.dumps(_uncertainty_flags(content)), created_at=now))
            if head is not None:
                moved = conn.execute(update(report_heads)
                                     .where(and_(report_heads.c.report_id == report_id,
                                                 report_heads.c.current_version == previous))
                                     .values(current_version=version, updated_at=now))
                if moved.rowcount != 1:
                    raise ReportError("VERSION_CONFLICT", f"{report_id} moved past v{previous} during this publish",
                                      operation_id=op)
        except ReportError:
            raise
        except IntegrityError as exc:
            if _is_unique_violation(exc):  # a concurrent publisher took this version number first
                raise ReportError("VERSION_CONFLICT", f"another publish of {report_id} won the race",
                                  operation_id=op) from exc
            raise ReportError("STORAGE_FAILURE", f"could not store {report_id}: {exc.orig}", operation_id=op,
                              retryable=True) from exc
        except SQLAlchemyError as exc:
            raise ReportError("STORAGE_FAILURE", f"could not store {report_id}: {exc}", operation_id=op,
                              retryable=True) from exc
        return VersionRecord(
            report_id=report_id, incident_id=incident_id, version=version, previous_version=previous,
            content_ref=f"sha256:{digest}", content_digest=digest, source_investigation_run_id=run_id,
            source_correlation_run_id=inputs.correlation.run_id if inputs.correlation else None,
            created_at=now, review_cycle_id=revision_of.review_cycle_id if revision_of else None,
            evidence_ids=[e.evidence_id for e in content.evidence_register],
            uncertainty_flags=_uncertainty_flags(content))

    # ---- read -------------------------------------------------------------
    def get(self, conn: Connection, report_id: str, version: int | None = None) -> StoredReport:
        op = f"get:{report_id}"
        if version is None:
            version = conn.execute(select(report_heads.c.current_version)
                                   .where(report_heads.c.report_id == report_id)).scalar()
        row = None if version is None else conn.execute(_versions().where(and_(
            report_versions.c.report_id == report_id, report_versions.c.version == version))).first()
        if row is None:
            raise ReportError("NOT_FOUND", f"{report_id} v{version} does not exist" if version else
                              f"{report_id} does not exist", operation_id=op)
        body = conn.execute(select(report_contents.c.body_json)
                            .where(report_contents.c.content_digest == row.content_digest)).scalar()
        if body is None or hashlib.sha256(body.encode("utf-8")).hexdigest() != row.content_digest:
            raise ReportError("STORAGE_FAILURE", f"content of {report_id} v{version} failed its digest check",
                              operation_id=op)
        return StoredReport(record=_record(row), content=ReportContent.model_validate_json(body))

    def history(self, conn: Connection, report_id: str) -> list[VersionRecord]:
        rows = conn.execute(_versions().where(report_versions.c.report_id == report_id)
                            .order_by(report_versions.c.version)).all()
        return [_record(r) for r in rows]

    def catalog(self, conn: Connection) -> list[VersionRecord]:
        """The current version of every report, newest incident first."""
        rows = conn.execute(_versions().where(report_versions.c.version == report_heads.c.current_version)
                            .order_by(report_heads.c.created_at.desc(), report_heads.c.report_id)).all()
        return [_record(r) for r in rows]
