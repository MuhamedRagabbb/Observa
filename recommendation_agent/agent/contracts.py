"""Typed F4 contracts aligned with OBSERVA_SHARED_CONTRACTS_v1."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer, model_validator

SignalType = Literal["logs", "metrics", "traces"]
ExecutionStatus = Literal["pending", "running", "completed", "partial", "inconclusive", "failed"]
ActionType = Literal["remediation", "verification", "investigation"]
ValidationStatus = Literal["supported", "unsupported", "conditional"]
UrgencyLevel = Literal["low", "medium", "high", "critical"]
RiskLevel = Literal["low", "medium", "high"]
AvailabilityStatus = Literal["available", "unavailable", "expired"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ErrorEnvelope(_StrictModel):
    """Cross-feature typed error envelope."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool
    operation_id: str = Field(min_length=1)
    details: dict[str, Any] | None = None


class EvidenceLink(_StrictModel):
    """Claim-to-evidence relation preserving reference, relevance, and support layers."""

    evidence_id: str = Field(min_length=1)
    relation: Literal["supports", "contradicts", "context_only", "unknown"]
    reference_valid: Union[bool, Literal["unknown"]]
    relevance_status: Literal["relevant", "irrelevant", "unknown"]
    support_status: Literal["supported", "contradicted", "unverified"]


class EvidenceRef(_FlexibleModel):
    """Resolvable telemetry evidence reference per Shared Contracts v1."""

    evidence_id: str = Field(min_length=1)
    signal_type: SignalType
    service: str = Field(min_length=1)
    source_record_ref: str | None = None
    query_ref: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    availability_status: AvailabilityStatus
    trace_id: str | None = None
    span_id: str | None = None
    claim: str | None = None
    indicator: str | None = None
    snapshot_id: str | None = None

    @field_validator("start_time", "end_time")
    @classmethod
    def _require_utc_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("Timestamps must be timezone-aware ISO 8601 UTC.")
        return value

    @model_validator(mode="after")
    def _check_availability_and_window(self) -> "EvidenceRef":
        if self.end_time is not None and self.end_time < self.start_time:
            raise ValueError("EvidenceRef end_time must not precede start_time.")
        if self.availability_status == "available" and not (self.source_record_ref or self.query_ref):
            raise ValueError("Available EvidenceRef requires source_record_ref or query_ref.")
        return self


class CorrelationCandidateInput(_FlexibleModel):
    """Inbound F3 root-cause candidate."""

    candidate_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    uncertainty: str | None = None
    service: str | None = None
    signals: list[SignalType] = Field(default_factory=list)


class MissingEvidenceInput(_FlexibleModel):
    """Inbound gap reported by F2/F3."""

    required: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    requested_from: str = Field(min_length=1)
    status: str = Field(min_length=1)


class F3CorrelationInput(_FlexibleModel):
    """Inbound F3 -> F4 CorrelationResult contract."""

    incident_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    root_cause_candidates: list[CorrelationCandidateInput] = Field(default_factory=list)
    leading_candidate_id: str | None = None
    missing_evidence: list[MissingEvidenceInput | dict[str, Any]] = Field(default_factory=list)
    status: Literal["completed", "partial", "inconclusive", "failed"]
    follow_up_requests: list[dict[str, Any]] = Field(default_factory=list)
    error: ErrorEnvelope | None = None


class IncidentWindow(_FlexibleModel):
    """Incident time bounds and affected topology context."""

    start_time: datetime
    end_time: datetime | None = None
    affected_services: list[str] = Field(default_factory=list)
    dependencies: list[tuple[str, str]] = Field(default_factory=list)

    @field_validator("start_time", "end_time")
    @classmethod
    def _require_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("IncidentWindow timestamps must be timezone-aware UTC.")
        return value


class ObservationSummary(_FlexibleModel):
    """Compact observation context forwarded from F3 internal state when available."""

    finding_id: str
    signal: SignalType
    service: str
    indicator: str | None = None
    claim: str
    evidence_ids: list[str] = Field(default_factory=list)
    snapshot_id: str | None = None
    relation: Literal["supports", "contradicts"] = "supports"
    support_status: Literal["supported", "contradicted"] = "supported"


class CorrelationContext(_FlexibleModel):
    """Normalized F3 context consumed by F4 option generation, validation, and ranking."""

    incident_id: str = Field(min_length=1)
    correlation_run_id: str = Field(min_length=1)
    status: Literal["completed", "partial", "inconclusive", "failed"]
    root_cause_candidates: list[CorrelationCandidateInput] = Field(default_factory=list)
    leading_candidate_id: str | None = None
    missing_evidence: list[MissingEvidenceInput] = Field(default_factory=list)
    follow_up_requests: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[ObservationSummary] = Field(default_factory=list)
    unresolved_evidence_ids: list[str] = Field(default_factory=list)
    affected_services: list[str] = Field(default_factory=list)
    dependencies: list[tuple[str, str]] = Field(default_factory=list)
    window: IncidentWindow | None = None
    opening_alert: dict[str, str] | None = None
    error: ErrorEnvelope | None = None


class ApprovedKnowledge(_FlexibleModel):
    """F5 approved historical incident record."""

    knowledge_id: str = Field(min_length=1)
    source_incident_id: str = Field(min_length=1)
    approved_report_id: str = Field(min_length=1)
    approved_version: int = Field(ge=1)
    approval_decision_id: str = Field(min_length=1)
    approved_at: datetime
    findings: list[dict[str, Any] | str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    reviewed_assessment: str = Field(min_length=1)
    verified_root_cause: str | None = None
    accepted_recommendations: list[dict[str, Any] | str] = Field(default_factory=list)
    record_version: int = Field(default=1, ge=1)
    is_current_approved_version: bool = True
    affected_services: list[str] = Field(default_factory=list)
    relevance_basis: str | dict[str, Any] | None = None


class MemorySearchResult(_StrictModel):
    """F5 -> F4 search result distinguishing ok+[] from failed."""

    status: Literal["ok", "failed"]
    items: list[ApprovedKnowledge] = Field(default_factory=list)
    error: ErrorEnvelope | None = None


class ProposedOption(_StrictModel):
    """Internal F4 recommendation option produced in F4-US-01."""

    recommendation_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    action_type: ActionType
    target_service: str | None = None
    candidate_id: str | None = None
    expected_impact: str = Field(min_length=1)
    risk_or_caveat: str | None = None
    urgency: UrgencyLevel = "medium"
    risk_level: RiskLevel = "medium"
    cited_evidence_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    historical_reference_ids: list[str] = Field(default_factory=list)


class EvidenceCheckDetail(_StrictModel):
    """Per-evidence validation trace produced by F4-US-02."""

    evidence_id: str = Field(min_length=1)
    reference_valid: Union[bool, Literal["unknown"]]
    availability_status: Literal["available", "unavailable", "expired", "unknown"]
    relevance_status: Literal["relevant", "irrelevant", "unknown"]
    support_status: Literal["supported", "contradicted", "unverified"]
    relation: Literal["supports", "contradicts", "context_only", "unknown"]
    reason: str = Field(min_length=1)


class ValidationRecord(_StrictModel):
    """Validation outcome for one recommendation option (F4-US-02)."""

    recommendation_id: str = Field(min_length=1)
    action_type: ActionType
    validation: ValidationStatus
    support_strength: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    checks: list[EvidenceCheckDetail] = Field(default_factory=list)


class RankedOptionDetail(_StrictModel):
    """Score and deterministic ranking explanation for a validated option (F4-US-03)."""

    recommendation_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    score: float
    validation: ValidationStatus
    action_type: ActionType
    explanation: str = Field(min_length=1)


class RecommendationState(_StrictModel):
    """Complete internal F4 state persisted for inspection and F7 handoff."""

    incident_id: str = Field(min_length=1)
    correlation_run_id: str = Field(min_length=1)
    recommendation_options: list[ProposedOption] = Field(default_factory=list)
    validation_results: list[ValidationRecord] = Field(default_factory=list)
    ranked_recommendation_ids: list[str] = Field(default_factory=list)
    selected_recommendations: list[str] = Field(default_factory=list)
    top_recommendation_id: str | None = None
    ranking_details: list[RankedOptionDetail] = Field(default_factory=list)
    memory_status: Literal["ok", "failed", "not_queried"] = "not_queried"
    memory_items_count: int = 0
    agent_status: Literal["completed", "partial", "inconclusive", "failed"]
    warnings: list[str] = Field(default_factory=list)
    error: ErrorEnvelope | None = None


class ContractRecommendationOption(_StrictModel):
    """Public F4 -> F7 recommendation option per Shared Contracts v1."""

    recommendation_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    action_type: ActionType
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    risk_or_caveat: str | None = None
    validation: ValidationStatus
    rationale: str = Field(min_length=1)


class RecommendationResult(_StrictModel):
    """Public F4 -> F7 handoff contract per OBSERVA_SHARED_CONTRACTS_v1."""

    incident_id: str = Field(min_length=1)
    correlation_run_id: str = Field(min_length=1)
    recommendation_options: list[ContractRecommendationOption] = Field(default_factory=list)
    ranked_recommendation_ids: list[str] = Field(default_factory=list)
    top_recommendation_id: str | None = None
    status: Literal["completed", "partial", "inconclusive", "failed"]
    error: ErrorEnvelope | None = None

    @model_serializer(mode="wrap")
    def serialize_contract(self, handler):
        data = handler(self)
        if self.error is None:
            data.pop("error", None)
        return data


def parse_utc(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _infer_service_from_description(description: str) -> str | None:
    if ":" in description:
        prefix = description.split(":", 1)[0].strip()
        if prefix and " " not in prefix:
            return prefix
    return None


def _normalize_missing_evidence(items: list[MissingEvidenceInput | dict[str, Any]]) -> list[MissingEvidenceInput]:
    normalized: list[MissingEvidenceInput] = []
    for item in items:
        if isinstance(item, MissingEvidenceInput):
            normalized.append(item)
        elif isinstance(item, dict):
            normalized.append(
                MissingEvidenceInput(
                    required=str(item.get("required") or "unspecified evidence"),
                    reason=str(item.get("reason") or "reported gap"),
                    requested_from=str(item.get("requested_from") or "logs"),
                    status=str(item.get("status") or "unavailable"),
                )
            )
    return normalized


def build_correlation_context(
    correlation: F3CorrelationInput | dict[str, Any],
    *,
    f3_state: dict[str, Any] | None = None,
    window: IncidentWindow | None = None,
) -> CorrelationContext:
    """Adapt F3's contract output (and optional internal F3 state) into a CorrelationContext."""
    parsed = (
        correlation
        if isinstance(correlation, F3CorrelationInput)
        else F3CorrelationInput.model_validate(correlation)
    )
    candidates: list[CorrelationCandidateInput] = []
    for cand in parsed.root_cause_candidates:
        service = cand.service or _infer_service_from_description(cand.description)
        signals = list(cand.signals)
        if not signals:
            inferred = []
            for link in cand.evidence_links:
                if link.support_status == "supported" and link.reference_valid is True:
                    parts = link.evidence_id.split("/", 2)
                    if len(parts) == 3:
                        sig = parts[2].partition("#")[0]
                        if sig in ("logs", "metrics", "traces") and sig not in inferred:
                            inferred.append(sig)
            signals = inferred
        candidates.append(cand.model_copy(update={"service": service, "signals": signals}))

    observations: list[ObservationSummary] = []
    unresolved_ids: list[str] = []
    affected_services: list[str] = list(window.affected_services) if window else []
    dependencies: list[tuple[str, str]] = list(window.dependencies) if window else []
    opening_alert: dict[str, str] | None = None
    inferred_window = window

    if f3_state:
        for row in f3_state.get("correlated_findings") or []:
            observations.append(ObservationSummary.model_validate(row))
        for item in f3_state.get("unresolved_evidence") or []:
            eid = item.get("evidence_id") if isinstance(item, dict) else getattr(item, "evidence_id", None)
            if eid:
                unresolved_ids.append(str(eid))
        history = (f3_state.get("report") or {}).get("history") or f3_state.get("history")
        if isinstance(history, dict):
            opening_alert = history.get("opening_alert")
            for svc in history.get("affected_services") or []:
                if svc not in affected_services:
                    affected_services.append(svc)
            for dep in history.get("dependencies") or []:
                caller, callee = dep.get("caller"), dep.get("callee")
                if caller and callee and (caller, callee) not in dependencies:
                    dependencies.append((caller, callee))
            if inferred_window is None and history.get("first_observed_at"):
                start = parse_utc(history["first_observed_at"])
                end = parse_utc(history.get("resolved_at"))
                if start is not None:
                    inferred_window = IncidentWindow(
                        start_time=start,
                        end_time=end,
                        affected_services=list(affected_services),
                        dependencies=list(dependencies),
                    )

    for cand in candidates:
        if cand.service and cand.service not in affected_services:
            affected_services.append(cand.service)
    for obs in observations:
        if obs.service and obs.service not in affected_services:
            affected_services.append(obs.service)

    return CorrelationContext(
        incident_id=parsed.incident_id,
        correlation_run_id=parsed.run_id,
        status=parsed.status,
        root_cause_candidates=candidates,
        leading_candidate_id=parsed.leading_candidate_id,
        missing_evidence=_normalize_missing_evidence(parsed.missing_evidence),
        follow_up_requests=list(parsed.follow_up_requests),
        observations=observations,
        unresolved_evidence_ids=sorted(set(unresolved_ids)),
        affected_services=affected_services,
        dependencies=dependencies,
        window=inferred_window,
        opening_alert=opening_alert,
        error=parsed.error,
    )
