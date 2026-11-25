"""Ports to the features F6 hands off to. Real adapters replace the mocks without touching F6."""
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from observa_investigation.contracts import FeedbackIntent, FollowUpRequest
from observa_reports.inputs import UtcDatetime

MEMORY_EVENT = "memory.approved_report.v1"


class ApprovedReportHandoff(BaseModel):
    """What F5 receives for an approved version. Human approval is not objective root-cause
    verification; F5 stores it as approved knowledge with this provenance."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_id: str
    incident_id: str
    report_id: str
    version: int
    content_ref: str
    content_digest: str
    approved_at: UtcDatetime
    reviewer_id: str
    evidence_ids: list[str]


class MemorySink(Protocol):
    def store(self, handoff: ApprovedReportHandoff) -> None:
        """Store approved knowledge. Must be idempotent on handoff.decision_id."""


class Reinvestigator(Protocol):
    def request(self, follow_up: FollowUpRequest, intent: FeedbackIntent) -> None:
        """Ask F2 for a targeted follow-up. Must be idempotent on follow_up.request_id. F2 answers later
        through the revision intake (a new report version) or by reporting the request failed."""
