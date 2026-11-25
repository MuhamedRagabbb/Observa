"""F2 contract types, following OBSERVA_SHARED_CONTRACTS_v1.

The contracts are PROPOSED, not team-approved. Keep names and enums exactly as
in the contract document and change them only together with the other owners.
"""
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

SignalType = Literal["logs", "metrics", "traces"]
ExecutionStatus = Literal["pending", "running", "completed", "partial", "inconclusive", "failed"]
SIGNALS = ("logs", "metrics", "traces")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceLink(_Model):
    evidence_id: str
    relation: Literal["supports", "contradicts", "context_only", "unknown"]
    reference_valid: Union[bool, Literal["unknown"]]
    relevance_status: Literal["relevant", "irrelevant", "unknown"]
    support_status: Literal["supported", "contradicted", "unverified"]


class SignalFinding(_Model):
    indicator: Optional[str] = None  # Original metric rule, log event or span operation.
    finding_id: str
    signal_type: SignalType
    service: str
    claim: str
    claim_kind: Literal["observation", "inference"]
    evidence_links: List[EvidenceLink]
    execution_status: Literal["completed", "partial", "inconclusive", "failed"]
    uncertainty: Optional[str]


class MissingEvidence(_Model):
    required: str
    reason: str
    requested_from: SignalType
    status: str  # e.g. unavailable | failed | not_implemented


class EvidenceAnswer(_Model):
    signal: SignalType
    snapshot_id: str
    question: str
    answer: str
    evidence_ids: List[str] = Field(default_factory=list)


class SignalOutput(_Model):
    answers: List[EvidenceAnswer] = Field(default_factory=list)
    findings: List[SignalFinding] = Field(default_factory=list)
    status: ExecutionStatus


class InvestigationResult(_Model):
    """F2 -> F3. All three slots are always present, even when an agent failed."""
    incident_id: str
    run_id: str
    logs_output: SignalOutput
    metrics_output: SignalOutput
    traces_output: SignalOutput
    missing_evidence: List[MissingEvidence] = Field(default_factory=list)
    status: ExecutionStatus
    investigation_plan_id: str
    parent_run_id: Optional[str] = None


class FollowUpRequest(_Model):
    """F3/F6 -> F2. Targeted follow-up investigation request."""
    request_id: str
    incident_id: str
    origin: Literal["correlation", "human_feedback"]
    target_agents: List[str]
    objectives: List[str]
    evidence_needed: List[str] = Field(default_factory=list)
    signal_objectives: Dict[str, List[str]] = Field(default_factory=dict)
    source_run_id: str
    review_cycle_id: Optional[str] = None
    status: Literal["pending", "accepted", "rejected", "completed"] = "pending"


class RequestedChange(_Model):
    target: str
    action: str
    reason: str
    status: str = "pending"


class FeedbackIntent(_Model):
    """F6 -> F2. Structured reviewer intent."""
    feedback_id: str
    incident_id: str
    report_id: str
    report_version: int
    human_feedback: str
    summary_of_human_intent: Optional[str] = None
    requested_changes: List[RequestedChange] = Field(default_factory=list)
    agent_status: ExecutionStatus
    review_cycle_id: str


class ErrorEnvelope(_Model):
    """Cross-feature typed error envelope."""
    code: str
    message: str
    retryable: bool
    operation_id: str
    details: Optional[Dict[str, Any]] = None

class F2Error(ValueError):
    """Typed domain exception carrying a contract ErrorEnvelope."""
    def __init__(self, envelope: ErrorEnvelope):
        self.envelope = envelope
        super().__init__(f"[{envelope.code}] {envelope.message}")



class IncidentV1(BaseModel):
    """F1 -> F2/F7 contract incident."""
    model_config = ConfigDict(extra="ignore")
    incident_id: str
    title: str = "Untitled incident"
    alerts: List[str] = Field(default_factory=list)
    start_time: str                                    # R per shared contract
    end_time: Optional[str] = None
    affected_services: List[str]                       # R per shared contract
    severity: Optional[str] = None
    snapshot_id: Optional[str] = None

    def get(self, key: str, default: Any = None) -> Any:
        """Support dict-like get() access so investigate(IncidentV1(...)) never crashes."""
        val = getattr(self, key, None)
        return val if val is not None else default

    def __getitem__(self, key: str) -> Any:
        val = self.get(key)
        if val is not None:
            return val
        raise KeyError(key)
