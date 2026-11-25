"""The structured output of feedback interpretation (F6-US-02 `feedback_state`).

Validation needs the context `{"allowed_targets": set[str]}`: the finding, candidate and
recommendation IDs, signal names and affected services of the reviewed version. A target
outside that set is rejected, so neither the model nor the fake can invent something to change.
Actions are a closed set: there is no way to express "approve" or "write memory".
"""
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, ValidationInfo, model_validator

from observa_investigation.contracts import FeedbackIntent, RequestedChange
from observa_reports.content import ReportContent

Action = Literal["reinvestigate", "reassess_root_cause", "reassess_recommendation", "correct_report_text"]
SIGNALS = ("logs", "metrics", "traces")


def _allowed(info: ValidationInfo) -> set[str]:
    allowed = (info.context or {}).get("allowed_targets")
    if allowed is None:
        raise ValueError("allowed_targets validation context is required to check change targets")
    return allowed


class ChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    target: str = Field(min_length=1, description="exactly one of the report's target IDs or names")
    action: Action
    reason: str = Field(min_length=1, max_length=1000, description="the reviewer's own words for this change")

    @model_validator(mode="after")
    def _target_is_known(self, info: ValidationInfo) -> "ChangeRequest":
        allowed = _allowed(info)
        if self.target not in allowed:
            raise ValueError(f"unknown target {self.target!r}: use one of {sorted(allowed)}")
        return self


class InterpretedFeedback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    summary_of_human_intent: str | None = Field(
        description="one faithful sentence of what the reviewer asks for; null when inconclusive")
    requested_changes: list[ChangeRequest]
    agent_status: Literal["completed", "inconclusive"]

    @model_validator(mode="after")
    def _status_matches_changes(self, info: ValidationInfo) -> "InterpretedFeedback":
        _allowed(info)
        if self.agent_status == "completed" and not self.requested_changes:
            raise ValueError("agent_status 'completed' needs at least one requested change; "
                             "use 'inconclusive' when the intent is unclear")
        if self.agent_status == "inconclusive" and self.requested_changes:
            raise ValueError("agent_status 'inconclusive' must not list requested changes")
        return self


INCONCLUSIVE = {"summary_of_human_intent": None, "requested_changes": [], "agent_status": "inconclusive"}


def inconclusive(targets: set[str]) -> InterpretedFeedback:
    return InterpretedFeedback.model_validate(INCONCLUSIVE, context={"allowed_targets": targets})


class InterpretationResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    # Already validated against the report's targets; re-validation here would lack that context.
    output: Annotated[InterpretedFeedback, SkipValidation]
    attempts: int = Field(ge=0)  # 0: nothing to send (empty feedback), no model call made
    raw: str | None = None  # the last unusable model output, kept for review when retries ran out
    interpreter: str

    @model_validator(mode="after")
    def _validated_output(self) -> "InterpretationResult":
        if not isinstance(self.output, InterpretedFeedback):
            raise ValueError("output must be an InterpretedFeedback validated against the report's targets")
        return self


class FeedbackInterpreter(Protocol):
    def interpret(self, feedback: str, report: ReportContent, allowed_targets: set[str]) -> InterpretationResult:
        ...


def allowed_targets(report: ReportContent) -> set[str]:
    targets = set(SIGNALS) | set(report.overview.affected_services)
    for signal in SIGNALS:
        targets |= {f.finding_id for f in getattr(report.findings, signal).findings}
        targets |= {f.service for f in getattr(report.findings, signal).findings}
    causes = ([report.root_cause.leading] if report.root_cause.leading else []) + report.root_cause.alternatives
    targets |= {c.candidate_id for c in causes}
    recs = report.recommendations.ranked + report.recommendations.other_options
    targets |= {r.recommendation_id for r in recs}
    return {t for t in targets if t}


def to_feedback_intent(result: InterpretationResult, *, feedback_id: str, incident_id: str, report_id: str,
                       report_version: int, human_feedback: str, review_cycle_id: str) -> FeedbackIntent:
    """IDs and the original feedback are set here by code, never taken from the model."""
    return FeedbackIntent(
        feedback_id=feedback_id, incident_id=incident_id, report_id=report_id, report_version=report_version,
        human_feedback=human_feedback, summary_of_human_intent=result.output.summary_of_human_intent,
        requested_changes=[RequestedChange(target=c.target, action=c.action, reason=c.reason, status="pending")
                           for c in result.output.requested_changes],
        agent_status=result.output.agent_status, review_cycle_id=review_cycle_id)
