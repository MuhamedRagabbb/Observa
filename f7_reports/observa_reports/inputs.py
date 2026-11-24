"""Inbound contract models: what F7 accepts from F1-F4 (OBSERVA_SHARED_CONTRACTS_v1).

Every model is strict (unknown fields rejected) and every timestamp must be
timezone-aware UTC (contract invariants 2 and 7). F2 types are reused from
observa_investigation.contracts rather than redefined.
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, PlainSerializer, model_validator

from observa_investigation.contracts import (
    ErrorEnvelope, EvidenceLink, FollowUpRequest, InvestigationResult, MissingEvidence,
)


def _require_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC (offset +00:00 or Z)")
    return value.astimezone(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc), PlainSerializer(_iso_z, return_type=str)]
NonEmpty = Annotated[str, Field(min_length=1)]
SignalType = Literal["logs", "metrics", "traces"]
Severity = Literal["low", "medium", "high", "critical"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ---- F1 ------------------------------------------------------------------
class AlertIn(_Strict):
    alert_id: NonEmpty
    signal_type: SignalType
    service: NonEmpty
    severity: Severity
    timestamp: UtcDatetime
    description: str
    evidence_ids: list[str] = Field(default_factory=list)
    first_seen: UtcDatetime | None = None
    last_seen: UtcDatetime | None = None

    @model_validator(mode="after")
    def _window(self):
        if self.first_seen and self.last_seen and self.last_seen < self.first_seen:
            raise ValueError("last_seen must not be earlier than first_seen")
        return self


class IncidentIn(_Strict):
    incident_id: NonEmpty
    title: NonEmpty
    alerts: list[NonEmpty]
    start_time: UtcDatetime
    end_time: UtcDatetime | None  # required key; null means ongoing / unknown
    affected_services: list[NonEmpty]
    severity: Severity | None = None
    scenario_run_id: str | None = None

    @model_validator(mode="after")
    def _window(self):
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("end_time must not be earlier than start_time")
        return self


class F1Output(_Strict):
    """What F1 hands to F7: the contract Incident plus the Alert records it groups."""
    incident: IncidentIn
    alerts: list[AlertIn]


# ---- F3 ------------------------------------------------------------------
class CandidateIn(_Strict):
    candidate_id: NonEmpty
    description: NonEmpty
    evidence_links: list[EvidenceLink]
    uncertainty: str | None


class CorrelationResultIn(_Strict):
    incident_id: NonEmpty
    run_id: NonEmpty
    root_cause_candidates: list[CandidateIn]
    leading_candidate_id: str | None
    missing_evidence: list[MissingEvidence]
    status: Literal["completed", "partial", "inconclusive", "failed"]
    follow_up_requests: list[FollowUpRequest]
    error: ErrorEnvelope | None = None

    @model_validator(mode="after")
    def _consistency(self):
        ids = [c.candidate_id for c in self.root_cause_candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("root_cause_candidates must have unique candidate_id values")
        if self.leading_candidate_id is not None and self.leading_candidate_id not in ids:
            raise ValueError("leading_candidate_id must name one of root_cause_candidates")
        if self.status in ("inconclusive", "failed") and self.leading_candidate_id is not None:
            raise ValueError(f"leading_candidate_id must be null when status is {self.status}")
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("error is required when status is failed, and only then")
        return self


# ---- F4 ------------------------------------------------------------------
class RecommendationOptionIn(_Strict):
    recommendation_id: NonEmpty
    action: NonEmpty
    action_type: Literal["remediation", "verification", "investigation"]
    evidence_links: list[EvidenceLink]
    risk_or_caveat: str | None
    validation: Literal["supported", "unsupported", "conditional"]
    rationale: str


class RecommendationResultIn(_Strict):
    incident_id: NonEmpty
    correlation_run_id: NonEmpty
    recommendation_options: list[RecommendationOptionIn]
    ranked_recommendation_ids: list[str]
    top_recommendation_id: str | None
    status: Literal["completed", "partial", "inconclusive", "failed"]

    @model_validator(mode="after")
    def _ranking(self):
        ids = {o.recommendation_id for o in self.recommendation_options}
        ranked = self.ranked_recommendation_ids
        if len(ranked) != len(set(ranked)) or not set(ranked) <= ids:
            raise ValueError("ranked_recommendation_ids must be unique ids of recommendation_options")
        if self.top_recommendation_id is not None and (not ranked or ranked[0] != self.top_recommendation_id):
            raise ValueError("top_recommendation_id must be null or the first ranked recommendation")
        return self


# ---- Bundle --------------------------------------------------------------
class ReportInputs(_Strict):
    """Everything F7 needs to assemble one report version. Missing upstream outputs are null."""
    incident: IncidentIn
    alerts: list[AlertIn]
    investigation: InvestigationResult | None
    correlation: CorrelationResultIn | None
    recommendations: RecommendationResultIn | None

    @model_validator(mode="after")
    def _same_incident(self):
        iid = self.incident.incident_id
        for name in ("investigation", "correlation", "recommendations"):
            part = getattr(self, name)
            if part is not None and part.incident_id != iid:
                raise ValueError(f"{name}.incident_id {part.incident_id!r} does not match incident {iid!r}")
        unknown = {a.alert_id for a in self.alerts} - set(self.incident.alerts)
        if unknown:
            raise ValueError(f"alert records not grouped in this incident: {sorted(unknown)}")
        if self.correlation and self.recommendations and \
                self.recommendations.correlation_run_id != self.correlation.run_id:
            raise ValueError("recommendations.correlation_run_id does not match correlation.run_id")
        return self
