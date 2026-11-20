"""State and handoff types for the F3 graph."""
from typing import Literal

from pydantic import BaseModel
from langchain.agents import AgentState
from investigation.observa_investigation.contracts import (
    FollowUpRequest, InvestigationResult, MissingEvidence, SignalType,
)


class Observation(BaseModel):
    finding_id: str
    signal: SignalType
    service: str
    claim: str
    evidence_ids: list[str]
    snapshot_id: str


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


class IncidentHistory(BaseModel):
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
    memory: list[str]
    result: CorrelationResult
