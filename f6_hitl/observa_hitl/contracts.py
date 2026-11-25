"""F6 review contracts: the command a reviewer sends, the durable ReviewDecision (contract shape)
and the read-side summary. IDs are deterministic (contract invariant 1)."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from observa_reports.inputs import UtcDatetime

Decision = Literal["approve", "request_changes"]
ReviewStatus = Literal["pending_review", "changes_requested", "approved"]
MemorySyncStatus = Literal["not_requested", "pending", "stored", "failed"]
CycleState = Literal["pending_review", "approved", "changes_requested", "interpreting", "clarification_needed",
                     "reinvestigating", "superseded", "revision_failed", "limit_reached"]
# States in which a reviewer may decide, and which decisions each allows. revision_failed and
# limit_reached stop the loop: the reviewer may still approve the current version explicitly, but
# cannot start another round of changes.
CHANGEABLE_STATES = ("pending_review", "clarification_needed")
APPROVE_ONLY_STATES = ("revision_failed", "limit_reached")
DECIDABLE_STATES = CHANGEABLE_STATES + APPROVE_ONLY_STATES
# Cycles still in flight; a newer version of the report supersedes them.
OPEN_STATES = ("pending_review", "changes_requested", "interpreting", "clarification_needed", "reinvestigating")


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def cycle_id_for(report_id: str, version: int) -> str:
    return "CYCLE-" + _h(f"{report_id}|{version}")


def decision_id_for(report_id: str, version: int, idempotency_key: str) -> str:
    return "DEC-" + _h(f"{report_id}|{version}|{idempotency_key}")


def feedback_id_for(decision_id: str) -> str:
    return "FB-" + decision_id[4:]


def event_id_for(event_type: str, dedupe_key: str) -> str:
    return "EVT-" + _h(f"{event_type}|{dedupe_key}")


def request_id_for(review_cycle_id: str, feedback_id: str) -> str:
    return "REQ-" + _h(f"{review_cycle_id}|{feedback_id}")


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("naive datetimes are rejected (contract invariant 2)")
    # Fixed width (always microseconds) so stored timestamps compare correctly as text.
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class DecisionCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    report_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    decision: Decision
    feedback: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=200)

    def normalized_feedback(self) -> str | None:
        """Approve stores null; a change request keeps the reviewer's words, trimmed only."""
        if self.decision == "approve":
            return None
        return (self.feedback or "").strip() or None

    def request_hash(self, reviewer_id: str) -> str:
        body = {"report_id": self.report_id, "version": self.version, "decision": self.decision,
                "feedback": self.normalized_feedback(), "reviewer_id": reviewer_id}
        return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ReviewDecision(BaseModel):
    """Contract ReviewDecision: a durable, immutable event."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    decision_id: str
    idempotency_key: str
    incident_id: str
    report_id: str
    version: int
    reviewer_id: str
    decision: Decision
    feedback: str | None
    decided_at: UtcDatetime


class DecisionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)
    decision: ReviewDecision
    replayed: bool


class ReviewCycle(BaseModel):
    model_config = ConfigDict(frozen=True)
    review_cycle_id: str
    report_id: str
    report_version: int
    cycle_number: int
    state: CycleState
    state_version: int
    opened_at: UtcDatetime | None
    closed_at: UtcDatetime | None


class DecisionView(ReviewDecision):
    model_config = ConfigDict(frozen=True, extra="forbid")
    review_cycle_id: str
    memory_sync_status: MemorySyncStatus


class FeedbackIntentView(BaseModel):
    model_config = ConfigDict(frozen=True)
    feedback_id: str
    decision_id: str
    review_cycle_id: str
    human_feedback: str
    summary_of_human_intent: str | None
    requested_changes: list[dict]
    agent_status: str
    interpreter: str
    attempts: int
    created_at: UtcDatetime | None


class ReinvestigationView(BaseModel):
    model_config = ConfigDict(frozen=True)
    request_id: str
    review_cycle_id: str
    feedback_id: str
    status: Literal["requested", "completed", "failed"]
    f2_run_id: str | None
    last_error: str | None
    created_at: UtcDatetime | None
    updated_at: UtcDatetime | None


class ReviewSummary(BaseModel):
    model_config = ConfigDict(frozen=True)
    report_id: str
    current_version: int
    cycles: list[ReviewCycle]
    decisions: list[DecisionView]
    feedback_intents: list[FeedbackIntentView]
    reinvestigations: list[ReinvestigationView]
    max_review_cycles: int
    cycles_used: int  # counted from the database, so restarts never reset it
    limit_reached: bool
