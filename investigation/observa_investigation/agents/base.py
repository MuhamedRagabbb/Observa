"""The one interface the orchestrator knows about. Agents never touch orchestration state."""
from dataclasses import dataclass, field
from typing import List, Optional, Protocol

from pydantic import Field

from ..contracts import EvidenceAnswer, ExecutionStatus, MissingEvidence, SignalFinding, _Model


@dataclass(frozen=True)
class AgentTask:
    incident: dict
    snapshot_id: Optional[str]
    run_id: str
    objectives: List[str] = field(default_factory=list)
    attempt: int = 1
    follow_up: bool = False


class AgentResult(_Model):
    """What one signal agent hands back. status=failed means 'could not run': the orchestrator may retry."""
    answers: List[EvidenceAnswer] = Field(default_factory=list)
    status: ExecutionStatus
    findings: List[SignalFinding] = Field(default_factory=list)
    missing_evidence: List[MissingEvidence] = Field(default_factory=list)
    error: Optional[str] = None


class SignalAgent(Protocol):
    signal: str

    def run(self, task: AgentTask) -> AgentResult: ...


class UnavailableAgent:
    """Placeholder for an agent that is not built yet. Reports honest missing evidence, never fake facts."""

    def __init__(self, signal: str, reason: str):
        self.signal, self.reason = signal, reason

    def run(self, task: AgentTask) -> AgentResult:
        return AgentResult(
            status="inconclusive",
            missing_evidence=[MissingEvidence(
                required=f"{self.signal} analysis", reason=self.reason,
                requested_from=self.signal, status="not_implemented")],
        )
