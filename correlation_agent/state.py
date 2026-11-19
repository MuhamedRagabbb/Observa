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


class CorrelationState(AgentState, total=False):
    investigation: InvestigationResult
    observations: list[Observation]
    memory: list[str]
    result: CorrelationResult
