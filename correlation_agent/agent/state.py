"""State and handoff types for the F3 graph."""
from typing import Literal

from pydantic import BaseModel, Field, model_serializer
from langchain.agents import AgentState
from observa_investigation.contracts import (
    EvidenceAnswer, FollowUpRequest, InvestigationResult, MissingEvidence, SignalType,
)


class Observation(BaseModel):
    indicator: str | None = None
    finding_id: str
    signal: SignalType
    service: str
    claim: str
    evidence_ids: list[str]
    snapshot_id: str
    relation: Literal["supports", "contradicts"] = "supports"
    support_status: Literal["supported", "contradicted"] = "supported"


class UnresolvedEvidence(BaseModel):
    finding_id: str
    signal: SignalType
    service: str
    evidence_id: str
    relation: Literal["supports", "contradicts", "context_only", "unknown"]
    reason: str


class Candidate(BaseModel):
    service: str
    hypothesis: str
    snapshot_id: str
    signals: list[SignalType]
    finding_ids: list[str]
    evidence_ids: list[str]
    origin_in_trace: bool
    related_finding_ids: list[str]


class ReportStep(BaseModel):
    step: int
    kind: Literal["probable_origin", "inferred_effect", "observation"]
    follows_step: int | None
    service: str
    explanation: str
    evidence: list[Observation]


class Dependency(BaseModel):
    caller: str
    callee: str
    signal: SignalType
    evidence_id: str
    parent_evidence_id: str | None = None


class AlertPeak(BaseModel):
    service: str
    rule: str
    value: float
    unit: str
    recorded_at: str
    evidence_id: str


class OpeningAlert(BaseModel):
    service: str
    rule: str
    family: str


class IncidentHistory(BaseModel):
    opening_alert: OpeningAlert | None = None
    phase: Literal["initial", "final"]
    first_snapshot: str
    snapshot_id: str
    first_observed_at: str
    resolved_at: str | None
    affected_services: list[str]
    peaks: list[AlertPeak]
    dependencies: list[Dependency]
    release_findings: list[Observation]


class CorrelationReport(BaseModel):
    follow_up_stop_reason: Literal["repeated_request", "round_limit"] | None = None
    follow_up_answers: list[EvidenceAnswer] = Field(default_factory=list)
    schema_version: Literal["1.0"] = "1.0"
    incident_id: str
    source_run_id: str
    status: Literal["probable_cause", "needs_more_analysis"]
    summary: str
    history: IncidentHistory | None = None
    steps: list[ReportStep]
    missing_evidence: list[MissingEvidence]


class CorrelationResult(BaseModel):
    incident_id: str
    source_run_id: str
    agent_status: Literal["completed", "needs_more_analysis"]
    correlated_findings: list[Observation]
    unresolved_evidence: list[UnresolvedEvidence] = Field(default_factory=list)
    relevant_data_from_memory: list[str]
    root_cause_candidates: list[Candidate]
    final_root_cause: Candidate | None
    missing_evidence: list[MissingEvidence]
    follow_up_request: FollowUpRequest | None
    report: CorrelationReport


class CorrelationState(AgentState, total=False):
    investigation: InvestigationResult
    additional_investigations: list[InvestigationResult]
    history: IncidentHistory
    observations: list[Observation]
    unresolved_evidence: list[UnresolvedEvidence]
    memory: list[str]
    result: CorrelationResult


class ContractEvidenceLink(BaseModel):
    evidence_id: str
    relation: Literal["supports", "contradicts", "context_only", "unknown"]
    reference_valid: bool | Literal["unknown"]
    relevance_status: Literal["relevant", "irrelevant", "unknown"]
    support_status: Literal["supported", "contradicted", "unverified"]


class ContractCandidate(BaseModel):
    candidate_id: str
    description: str
    evidence_links: list[ContractEvidenceLink]
    uncertainty: str | None


class ContractErrorEnvelope(BaseModel):
    code: str
    message: str
    retryable: bool
    operation_id: str
    details: dict | None


class ContractCorrelationResult(BaseModel):
    """Public F3 handoff shape from the proposed shared contract v1."""
    incident_id: str
    run_id: str
    root_cause_candidates: list[ContractCandidate]
    leading_candidate_id: str | None
    missing_evidence: list[MissingEvidence]
    status: Literal["completed", "partial", "inconclusive", "failed"]
    follow_up_requests: list[FollowUpRequest]
    error: ContractErrorEnvelope | None = None

    @model_serializer(mode="wrap")
    def serialize_contract(self, handler):
        data = handler(self)
        if self.error is None:
            data.pop("error", None)
        return data
