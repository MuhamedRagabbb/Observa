"""F6-US-02 feedback workflow: the outbox handler for `feedback.interpret.v1`.

    changes_requested --(claim)--> interpreting --(model)--> clarification_needed | reinvestigating

1. A short transaction moves the cycle to `interpreting` (visible while the model runs) and reads
   the reviewer's feedback and the reviewed version's content.
2. The interpreter runs outside any transaction. A provider failure raises, so the outbox retries.
3. The returned database step commits with the outbox acknowledgement: the feedback_intents row,
   the cycle transition (compare-and-swap) and, for a completed interpretation only, one
   `reinvestigation.request.v1` event (dedupe = request_id). An inconclusive interpretation
   dispatches nothing: the reviewer is asked to clarify instead.

Interpretation never touches report content, versions or approvals.
"""
import json
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import Connection, Engine, select, update

from observa_investigation.contracts import FeedbackIntent, FollowUpRequest
from observa_reports.content import ReportContent
from observa_reports.service import ReportService

from .contracts import feedback_id_for, iso, request_id_for
from .interpreter import FeedbackInterpreter, allowed_targets, to_feedback_intent
from .interpreter.schema import SIGNALS, InterpretationResult
from .outbox import REINVESTIGATION_EVENT, DbStep, Handler, Outbox, _insert_ignore
from .tables import feedback_intents, review_cycles, review_decisions

INTERPRETABLE = ("changes_requested", "interpreting")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class StaleInterpretation(RuntimeError):
    """The cycle moved on while the model was running; the outbox retries and then finds nothing to do."""


def _signals_for(target: str, report: ReportContent) -> list[str]:
    if target in SIGNALS:
        return [target]
    for signal in SIGNALS:
        if any(f.finding_id == target for f in getattr(report.findings, signal).findings):
            return [signal]
    return list(SIGNALS)  # a service, cause or recommendation does not name a single signal


def follow_up_for(intent: FeedbackIntent, report: ReportContent, *, request_id: str,
                  source_run_id: str) -> FollowUpRequest:
    objectives, by_signal = [], {s: [] for s in SIGNALS}
    for change in intent.requested_changes:
        objective = f"{change.action} {change.target}: {change.reason}"
        objectives.append(objective)
        for signal in _signals_for(change.target, report):
            by_signal[signal].append(objective)
    return FollowUpRequest(
        request_id=request_id, incident_id=intent.incident_id, origin="human_feedback",
        target_agents=[s for s in SIGNALS if by_signal[s]], objectives=objectives,
        signal_objectives={s: o for s, o in by_signal.items() if o}, source_run_id=source_run_id,
        review_cycle_id=intent.review_cycle_id, status="pending")


def interpret_handler(engine: Engine, interpreter: FeedbackInterpreter, *,
                      clock: Callable[[], datetime] = _now, reports: ReportService | None = None) -> Handler:
    reports = reports or ReportService(clock=clock)
    outbox = Outbox(clock=clock)

    def handle(payload: dict) -> DbStep | None:
        decision_id, cycle_id = payload["decision_id"], payload["review_cycle_id"]
        with engine.begin() as conn:
            cycle = conn.execute(select(review_cycles).where(review_cycles.c.review_cycle_id == cycle_id)).one()
            if cycle.state not in INTERPRETABLE:
                return None  # already interpreted (a redelivery): acknowledge and do nothing
            if cycle.state == "changes_requested":
                conn.execute(update(review_cycles).where(
                    review_cycles.c.review_cycle_id == cycle_id, review_cycles.c.state == "changes_requested",
                ).values(state="interpreting", state_version=cycle.state_version + 1))
            decision = conn.execute(select(review_decisions).where(
                review_decisions.c.decision_id == decision_id)).one()
            stored = reports.get(conn, decision.report_id, decision.version)
        targets = allowed_targets(stored.content)
        result = interpreter.interpret(decision.feedback, stored.content, targets)
        feedback_id = feedback_id_for(decision_id)
        intent = to_feedback_intent(result, feedback_id=feedback_id, incident_id=decision.incident_id,
                                    report_id=decision.report_id, report_version=decision.version,
                                    human_feedback=decision.feedback, review_cycle_id=cycle_id)

        def record(conn: Connection) -> None:
            _store_intent(conn, intent, result, decision_id, iso(clock()))
            completed = intent.agent_status == "completed"
            moved = conn.execute(update(review_cycles).where(
                review_cycles.c.review_cycle_id == cycle_id, review_cycles.c.state == "interpreting",
            ).values(state="reinvestigating" if completed else "clarification_needed",
                     state_version=review_cycles.c.state_version + 1))
            if moved.rowcount != 1:
                raise StaleInterpretation(f"cycle {cycle_id} is no longer interpreting")
            if completed:
                request_id = request_id_for(cycle_id, feedback_id)
                follow_up = follow_up_for(intent, stored.content, request_id=request_id,
                                          source_run_id=stored.record.source_investigation_run_id)
                outbox.enqueue(conn, REINVESTIGATION_EVENT, request_id, {
                    "request_id": request_id, "follow_up": follow_up.model_dump(mode="json"),
                    "feedback_intent": intent.model_dump(mode="json")})
        return record

    return handle


def _store_intent(conn: Connection, intent: FeedbackIntent, result: InterpretationResult, decision_id: str,
                  now: str) -> None:
    _insert_ignore(conn, feedback_intents, {
        "feedback_id": intent.feedback_id, "decision_id": decision_id, "review_cycle_id": intent.review_cycle_id,
        "human_feedback": intent.human_feedback, "summary_of_human_intent": intent.summary_of_human_intent,
        "requested_changes": json.dumps([c.model_dump(mode="json") for c in intent.requested_changes]),
        "agent_status": intent.agent_status, "interpreter": result.interpreter, "attempts": result.attempts,
        "raw_output": result.raw, "created_at": now})
