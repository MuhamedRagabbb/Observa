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


class SignalOutput(_Model):
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
