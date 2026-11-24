"""The structured report content (F7-US-01) and its canonical, content-addressed form.

ReportContent is everything a version shows; it carries no clock values or
generated IDs, so the same inputs always produce the same bytes and digest.
Version identity (report_id, version, created_at) lives outside the content.
"""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from observa_investigation.contracts import EvidenceLink

from .inputs import Severity, SignalType, UtcDatetime

SCHEMA_VERSION = "1.0"
DRAFT_NOTICE = ("Draft: pending human review. This report is not approved and has not been "
                "stored as approved incident knowledge.")


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Overview(_Frozen):
    incident_id: str
    title: str
    start_time: UtcDatetime
    end_time: UtcDatetime | None
    affected_services: list[str]
    severity: Severity | None


class AlertItem(_Frozen):
    alert_id: str
    signal_type: SignalType
    service: str
    severity: Severity
    timestamp: UtcDatetime
    description: str
    evidence_ids: list[str]


class AlertsSection(_Frozen):
    items: list[AlertItem]
    missing_alert_ids: list[str]  # grouped by F1 but no alert record supplied


class TimelineEvent(_Frozen):
    at: UtcDatetime | None
    kind: Literal["incident_started", "alert_raised", "alert_last_seen", "incident_ended", "alert_unrecorded"]
    description: str
    ref: str


class Timeline(_Frozen):
    events: list[TimelineEvent]   # timed, chronological
    untimed: list[TimelineEvent]  # known to exist, time not supplied: never given an invented time


class FindingItem(_Frozen):
    finding_id: str
    service: str
    claim: str
    claim_kind: Literal["observation", "inference"]
    indicator: str | None
    execution_status: str
    uncertainty: str | None
    evidence_links: list[EvidenceLink]


class SignalSection(_Frozen):
    status: str  # an execution_status, or "unavailable" when F2 supplied nothing
    findings: list[FindingItem]


class Findings(_Frozen):
    logs: SignalSection
    metrics: SignalSection
    traces: SignalSection


class CauseItem(_Frozen):
    candidate_id: str
    description: str
    uncertainty: str | None
    evidence_links: list[EvidenceLink]


class RootCause(_Frozen):
    status: Literal["leading", "inconclusive", "unavailable"]
    correlation_status: str | None
    correlation_run_id: str | None
    leading: CauseItem | None
    alternatives: list[CauseItem]


class Assessment(_Frozen):
    claim_type: Literal["finding", "root_cause_candidate", "recommendation"]
    claim_ref: str
    relation: str
    support_status: str
    relevance_status: str


class EvidenceEntry(_Frozen):
    evidence_id: str
    signal_type: SignalType | None
    snapshot_id: str | None
    services: list[str]
    reference_status: Literal["valid", "invalid", "unresolved"]
    assessments: list[Assessment]


class MissingItem(_Frozen):
    source: Literal["F2", "F3"]
    required: str
    reason: str
    requested_from: str
    status: str


class RecommendationItem(_Frozen):
    rank: int | None
    recommendation_id: str
    action: str
    action_type: Literal["remediation", "verification", "investigation"]
    validation: Literal["supported", "unsupported", "conditional"]
    rationale: str
    risk_or_caveat: str | None
    evidence_links: list[EvidenceLink]


class Recommendations(_Frozen):
    status: Literal["ranked", "no_validated_remediation", "unavailable"]
    recommendation_status: str | None
    ranked: list[RecommendationItem]
    other_options: list[RecommendationItem]
    caveats: list[str]


class ReportContent(_Frozen):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    overview: Overview
    executive_summary: str
    alerts: AlertsSection
    timeline: Timeline
    findings: Findings
    root_cause: RootCause
    evidence_register: list[EvidenceEntry]
    missing_evidence: list[MissingItem]
    recommendations: Recommendations
    limitations: list[str]
    draft_notice: str = DRAFT_NOTICE


def canonical_json(content: ReportContent) -> bytes:
    """Sorted keys, compact separators, UTF-8: the exact bytes that are hashed and stored."""
    return json.dumps(content.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(content: ReportContent) -> str:
    return hashlib.sha256(canonical_json(content)).hexdigest()
