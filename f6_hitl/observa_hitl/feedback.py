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

from observa_reports.inputs import ReportInputs
from observa_reports.service import RevisionRef, VersionRecord, report_id_for
from observa_reports.tables import report_heads, report_versions

from .contracts import feedback_id_for, iso, request_id_for
from .errors import F6Error
from .interpreter import FeedbackInterpreter, allowed_targets, to_feedback_intent
from .interpreter.schema import SIGNALS, InterpretationResult
from .outbox import REINVESTIGATION_EVENT, DbStep, Handler, Outbox, _insert_ignore
from .ports import Reinvestigator
from .tables import feedback_intents, reinvestigations, review_cycles, review_decisions

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


def _evidence_for(target: str, report: ReportContent) -> set[str]:
    """The evidence already cited for what the reviewer questioned: F2 re-checks it first."""
    findings = [f for s in SIGNALS for f in getattr(report.findings, s).findings]
    if target in SIGNALS:
        links = [l for f in getattr(report.findings, target).findings for l in f.evidence_links]
    else:
        links = [l for f in findings if target in (f.finding_id, f.service) for l in f.evidence_links]
        causes = ([report.root_cause.leading] if report.root_cause.leading else []) + report.root_cause.alternatives
        links += [l for c in causes if c.candidate_id == target for l in c.evidence_links]
        recs = report.recommendations.ranked + report.recommendations.other_options
        links += [l for r in recs if r.recommendation_id == target for l in r.evidence_links]
    return {l.evidence_id for l in links}


def follow_up_for(intent: FeedbackIntent, report: ReportContent, *, request_id: str,
                  source_run_id: str) -> FollowUpRequest:
    objectives, by_signal, evidence = [], {s: [] for s in SIGNALS}, set()
    for change in intent.requested_changes:
        objective = f"{change.action} {change.target}: {change.reason}"
        objectives.append(objective)
        evidence |= _evidence_for(change.target, report)
        for signal in _signals_for(change.target, report):
            by_signal[signal].append(objective)
    return FollowUpRequest(
        request_id=request_id, incident_id=intent.incident_id, origin="human_feedback",
        target_agents=[s for s in SIGNALS if by_signal[s]], objectives=objectives,
        evidence_needed=sorted(evidence), signal_objectives={s: o for s, o in by_signal.items() if o},
        source_run_id=source_run_id, review_cycle_id=intent.review_cycle_id, status="pending")


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


# ---- F6-US-03: re-investigation and re-review ---------------------------------------------------

def reinvestigation_handler(engine: Engine, reinvestigator: Reinvestigator, *,
                            clock: Callable[[], datetime] = _now) -> Handler:
    """`reinvestigation.request.v1` -> F2. The reinvestigations row (stable request_id, one per cycle)
    is written before F2 is called, so F2's answer always finds it; a redelivery re-sends the same
    request_id, which F2 must treat idempotently. A cycle that moved on (superseded) dispatches nothing."""
    def handle(payload: dict) -> None:
        follow_up = FollowUpRequest.model_validate(payload["follow_up"])
        intent = FeedbackIntent.model_validate(payload["feedback_intent"])
        with engine.begin() as conn:
            state = conn.execute(select(review_cycles.c.state).where(
                review_cycles.c.review_cycle_id == follow_up.review_cycle_id)).scalar_one()
            if state != "reinvestigating":
                return None
            now = iso(clock())
            _insert_ignore(conn, reinvestigations, {
                "request_id": follow_up.request_id, "review_cycle_id": follow_up.review_cycle_id,
                "feedback_id": intent.feedback_id, "status": "requested", "f2_run_id": None, "last_error": None,
                "created_at": now, "updated_at": now})
        reinvestigator.request(follow_up, intent)  # outside any transaction; a failure is retried
        return None
    return handle


def handle_revision_ready(conn: Connection, report_id: str, inputs: ReportInputs, review_cycle_id: str, *,
                          gate, reports: ReportService, clock: Callable[[], datetime] = _now) -> VersionRecord:
    """F2's revised findings for a cycle in `reinvestigating`: in ONE transaction F7 publishes vN+1
    (CAS on the head), the old cycle becomes `superseded`, the request `completed`, and vN+1 opens a
    new cycle in `pending_review`. The revision is never approved automatically."""
    op = f"revision:{review_cycle_id}"
    cycle = conn.execute(select(review_cycles).where(review_cycles.c.review_cycle_id == review_cycle_id)).first()
    if cycle is None:
        raise F6Error("NOT_FOUND", f"Review cycle {review_cycle_id} does not exist.", operation_id=op)
    if cycle.report_id != report_id or report_id_for(inputs.incident.incident_id) != report_id:
        raise F6Error("VERSION_CONFLICT", "The revision does not belong to this report.", operation_id=op)
    if cycle.state == "superseded":  # a redelivered result: return the version it already produced
        done = conn.execute(select(report_versions.c.version).where(
            report_versions.c.report_id == report_id, report_versions.c.revision_of_cycle_id == review_cycle_id,
        )).scalar_one_or_none()
        if done is not None:
            return reports.get(conn, report_id, done).record
    if cycle.state != "reinvestigating":
        raise F6Error("VERSION_CONFLICT", f"Cycle {review_cycle_id} is {cycle.state}, not awaiting a revision.",
                      operation_id=op, details={"state": cycle.state})
    head = conn.execute(select(report_heads.c.current_version).where(report_heads.c.report_id == report_id))\
        .scalar_one()
    if head != cycle.report_version:
        raise F6Error("VERSION_CONFLICT", "A newer version was published meanwhile.", operation_id=op,
                      details={"current_version": head})
    record = reports.publish(conn, inputs, revision_of=RevisionRef(
        review_cycle_id=review_cycle_id, expected_previous=cycle.report_version))
    now = iso(clock())
    moved = conn.execute(update(review_cycles).where(
        review_cycles.c.review_cycle_id == review_cycle_id, review_cycles.c.state == "reinvestigating",
    ).values(state="superseded", state_version=review_cycles.c.state_version + 1, closed_at=now))
    if moved.rowcount != 1:
        raise F6Error("VERSION_CONFLICT", "The cycle changed while the revision was being stored.", operation_id=op)
    conn.execute(update(reinvestigations).where(reinvestigations.c.review_cycle_id == review_cycle_id).values(
        status="completed", f2_run_id=record.source_investigation_run_id, last_error=None, updated_at=now))
    gate.open(conn, report_id, record.version)
    return record


def handle_revision_failed(conn: Connection, request_id: str, error: str, *,
                           clock: Callable[[], datetime] = _now) -> None:
    """F2 could not revise: the cycle stops in `revision_failed`, vN stays current and the error is kept."""
    op = f"revision-failed:{request_id}"
    row = conn.execute(select(reinvestigations).where(reinvestigations.c.request_id == request_id)).first()
    if row is None:
        raise F6Error("NOT_FOUND", f"Re-investigation {request_id} does not exist.", operation_id=op)
    if row.status == "failed":
        return  # already recorded (a redelivered failure)
    if row.status != "requested":
        raise F6Error("VERSION_CONFLICT", f"Re-investigation {request_id} already {row.status}.", operation_id=op)
    now = iso(clock())
    moved = conn.execute(update(review_cycles).where(
        review_cycles.c.review_cycle_id == row.review_cycle_id, review_cycles.c.state == "reinvestigating",
    ).values(state="revision_failed", state_version=review_cycles.c.state_version + 1, closed_at=now))
    if moved.rowcount != 1:
        raise F6Error("VERSION_CONFLICT", "The review cycle is no longer awaiting this re-investigation.",
                      operation_id=op)
    conn.execute(update(reinvestigations).where(reinvestigations.c.request_id == request_id).values(
        status="failed", last_error=(error or "unspecified failure")[:1000], updated_at=now))
