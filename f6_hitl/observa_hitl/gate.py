"""F6 ReviewGate (F6-US-01): the only way a report version becomes approved or gets changes requested.

`decide` runs inside the caller's unit of work, so the authorization result, the idempotency
check, the compare-and-swap on the review cycle, the decision row and its outbox event commit
together or not at all. `submit` wraps that for HTTP callers and audits a rejected attempt in a
separate transaction after the decision transaction rolled back, so the audit survives.
"""
import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from sqlalchemy import Connection, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from observa_reports.service import ReportService
from observa_reports.tables import report_heads, report_versions

from .auth import Reviewer
from .contracts import (APPROVE_ONLY_STATES, DECIDABLE_STATES, OPEN_STATES, DecisionCommand, DecisionOutcome,
                        DecisionView, FailedDelivery, FeedbackIntentView, ReinvestigationView, ReviewCycle,
                        ReviewDecision, ReviewSummary, cycle_id_for, decision_id_for, iso, request_id_for)
from .errors import F6Error
from .outbox import INTERPRET_EVENT, REINVESTIGATION_EVENT, Outbox
from .ports import MEMORY_EVENT, ApprovedReportHandoff
from .projections import memory_sync_status
from .tables import feedback_intents, outbox_events, reinvestigations, review_audit, review_cycles, review_decisions
UnitOfWork = Callable[[], AbstractContextManager[Connection]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cycle(row) -> ReviewCycle:
    return ReviewCycle(review_cycle_id=row.review_cycle_id, report_id=row.report_id,
                       report_version=row.report_version, cycle_number=row.cycle_number, state=row.state,
                       state_version=row.state_version, opened_at=row.opened_at, closed_at=row.closed_at)


def _decision(row) -> ReviewDecision:
    return ReviewDecision(decision_id=row.decision_id, idempotency_key=row.idempotency_key,
                          incident_id=row.incident_id, report_id=row.report_id, version=row.version,
                          reviewer_id=row.reviewer_id, decision=row.decision, feedback=row.feedback,
                          decided_at=row.decided_at)


class ReviewGate:
    def __init__(self, clock: Callable[[], datetime] = _now, *, reports: ReportService | None = None,
                 max_review_cycles: int = 3):
        self._clock = clock
        self._reports = reports or ReportService(clock=clock)
        self._outbox = Outbox(clock=clock)
        self.max_review_cycles = max_review_cycles

    # ---- opening ------------------------------------------------------------------------------
    def open(self, conn: Connection, report_id: str, version: int) -> ReviewCycle:
        """Open the review cycle for a published version (idempotent). It waits in pending_review
        until a reviewer decides; the row is durable, so the wait survives restarts. Opening a newer
        version supersedes every still-open cycle of older versions; decided cycles keep their state."""
        cycle_id = cycle_id_for(report_id, version)
        existing = conn.execute(select(review_cycles).where(review_cycles.c.review_cycle_id == cycle_id)).first()
        if existing is not None:
            return _cycle(existing)
        now = iso(self._clock())
        conn.execute(update(review_cycles).where(
            review_cycles.c.report_id == report_id, review_cycles.c.report_version < version,
            review_cycles.c.state.in_(OPEN_STATES),
        ).values(state="superseded", state_version=review_cycles.c.state_version + 1, closed_at=now))
        number = conn.execute(select(func.count()).select_from(review_cycles)
                              .where(review_cycles.c.report_id == report_id)).scalar_one() + 1
        conn.execute(insert(review_cycles).values(
            review_cycle_id=cycle_id, report_id=report_id, report_version=version, cycle_number=number,
            state="pending_review", state_version=0, opened_at=now, closed_at=None))
        return _cycle(conn.execute(select(review_cycles).where(review_cycles.c.review_cycle_id == cycle_id)).one())

    @staticmethod
    def cycles_used(conn: Connection, report_id: str) -> int:
        """Human review rounds consumed so far, read from the database (never caller-supplied): the first
        review plus every version F6 itself asked F2 to revise. Upstream updates (F1-F4 republishing new
        findings) open a review cycle but do not use up the reviewers' budget."""
        revisions = conn.execute(select(func.count()).select_from(report_versions).where(
            report_versions.c.report_id == report_id, report_versions.c.revision_of_cycle_id.is_not(None)))\
            .scalar_one()
        return 1 + revisions

    # ---- deciding -----------------------------------------------------------------------------
    def decide(self, conn: Connection, reviewer: Reviewer | None, cmd: DecisionCommand) -> DecisionOutcome:
        op = f"decide:{cmd.report_id}:v{cmd.version}"
        # 1. Authorization: only a reviewer, never a viewer or an anonymous caller.
        if reviewer is None or not reviewer.can_review:
            who = reviewer.reviewer_id if reviewer else "anonymous"
            raise F6Error("NOT_AUTHORIZED", f"{who} is not allowed to review reports.", operation_id=op)
        # 2. The feedback rule.
        feedback = cmd.normalized_feedback()
        if cmd.decision == "request_changes" and feedback is None:
            raise F6Error("VALIDATION_ERROR", "Request changes needs written feedback that is not empty.",
                          operation_id=op, details={"field": "feedback"})
        # 3. Lock the report head first. Under Postgres READ COMMITTED this serializes concurrent decisions
        #    for the report, so a second request with the same key waits here and then sees the first one's
        #    decision as a replay (instead of passing the lookup and failing later with a conflict).
        head = conn.execute(select(report_heads.c.current_version, report_heads.c.incident_id)
                            .where(report_heads.c.report_id == cmd.report_id).with_for_update()).first()
        if head is None:
            raise F6Error("NOT_FOUND", f"Report {cmd.report_id} does not exist.", operation_id=op)
        # 4. Idempotency: the same key and payload replays; the same key with another payload conflicts.
        request_hash = cmd.request_hash(reviewer.reviewer_id)
        prior = conn.execute(select(review_decisions).where(
            review_decisions.c.idempotency_key == cmd.idempotency_key)).first()
        if prior is not None:
            if prior.request_hash != request_hash:
                raise F6Error("IDEMPOTENCY_CONFLICT", "This Idempotency-Key was already used for a different "
                              "decision.", operation_id=op, details={"decision_id": prior.decision_id})
            self._audit(conn, reviewer.reviewer_id, cmd, "replayed", None, prior.decision_id)
            return DecisionOutcome(decision=_decision(prior), replayed=True)
        # 5. Only the current head version's open cycle accepts a decision (compare-and-swap).
        if cmd.version != head.current_version:
            raise F6Error("VERSION_CONFLICT", f"Version {cmd.version} is not the current version.", operation_id=op,
                          details={"current_version": head.current_version})
        cycle = self.open(conn, cmd.report_id, cmd.version)
        if cycle.state not in DECIDABLE_STATES:
            raise F6Error("VERSION_CONFLICT", f"Version {cmd.version} is no longer awaiting a decision.",
                          operation_id=op, details={"state": cycle.state, "current_version": head.current_version})
        if cmd.decision == "request_changes" and cycle.state in APPROVE_ONLY_STATES:
            if cycle.state == "limit_reached":
                raise F6Error("RETRY_LIMIT_REACHED", f"The review limit of {self.max_review_cycles} rounds was "
                              "reached; this version can only be approved.", operation_id=op,
                              details={"state": cycle.state, "max_review_cycles": self.max_review_cycles})
            raise F6Error("VERSION_CONFLICT", "The re-investigation for this version failed; it can only be "
                          "approved as it stands.", operation_id=op, details={"state": cycle.state})
        limit_hit = (cmd.decision == "request_changes"
                     and self.cycles_used(conn, cmd.report_id) >= self.max_review_cycles)
        if cmd.decision == "approve":
            new_state = "approved"
        else:  # the decision is recorded either way; at the limit nothing is dispatched
            new_state = "limit_reached" if limit_hit else "changes_requested"
        now = self._clock()
        swapped = conn.execute(update(review_cycles).where(
            review_cycles.c.review_cycle_id == cycle.review_cycle_id,
            review_cycles.c.state.in_(DECIDABLE_STATES),
            review_cycles.c.state_version == cycle.state_version,
        ).values(state=new_state, state_version=cycle.state_version + 1,
                 closed_at=iso(now) if new_state in ("approved", "limit_reached") else None))
        if swapped.rowcount != 1:
            raise F6Error("VERSION_CONFLICT", "Another decision was recorded for this version first.",
                          operation_id=op, details={"current_version": head.current_version})
        # 6. The immutable decision.
        decision = ReviewDecision(
            decision_id=decision_id_for(cmd.report_id, cmd.version, cmd.idempotency_key),
            idempotency_key=cmd.idempotency_key, incident_id=head.incident_id, report_id=cmd.report_id,
            version=cmd.version, reviewer_id=reviewer.reviewer_id, decision=cmd.decision, feedback=feedback,
            decided_at=now)
        try:
            conn.execute(insert(review_decisions).values(
                **decision.model_dump(exclude={"decided_at"}), decided_at=iso(now), request_hash=request_hash,
                review_cycle_id=cycle.review_cycle_id))
        except IntegrityError as exc:  # a concurrent writer used the same key first (Postgres)
            raise F6Error("IDEMPOTENCY_CONFLICT", "This Idempotency-Key was just used by another request.",
                          operation_id=op) from exc
        # 7. Its durable handoff intent, in the same transaction.
        if cmd.decision == "approve":
            record = self._reports.get(conn, cmd.report_id, cmd.version).record
            handoff = ApprovedReportHandoff(
                decision_id=decision.decision_id, incident_id=record.incident_id, report_id=record.report_id,
                version=record.version, content_ref=record.content_ref, content_digest=record.content_digest,
                approved_at=now, reviewer_id=reviewer.reviewer_id, evidence_ids=record.evidence_ids)
            self._outbox.enqueue(conn, MEMORY_EVENT, decision.decision_id, handoff.model_dump(mode="json"))
        elif not limit_hit:
            self._outbox.enqueue(conn, INTERPRET_EVENT, decision.decision_id, {
                "decision_id": decision.decision_id, "review_cycle_id": cycle.review_cycle_id,
                "report_id": cmd.report_id, "version": cmd.version})
        self._audit(conn, reviewer.reviewer_id, cmd, "accepted", "limit_reached" if limit_hit else None,
                    decision.decision_id)
        return DecisionOutcome(decision=decision, replayed=False)

    def submit(self, uow: UnitOfWork, reviewer: Reviewer | None, cmd: DecisionCommand) -> DecisionOutcome:
        """decide() in its own unit of work; a rejection is audited after the rollback."""
        try:
            with uow() as conn:
                return self.decide(conn, reviewer, cmd)
        except F6Error as exc:
            with uow() as conn:
                self.record_rejection(conn, reviewer, cmd, exc)
            raise

    def record_rejection(self, conn: Connection, reviewer: Reviewer | None, cmd: DecisionCommand,
                         error: F6Error) -> None:
        self._audit(conn, reviewer.reviewer_id if reviewer else None, cmd, "rejected", error.envelope.code,
                    error.envelope.message)

    def _audit(self, conn, actor, cmd, outcome, reason_code, detail) -> None:
        conn.execute(insert(review_audit).values(
            at=iso(self._clock()), actor=actor, action="decide", report_id=cmd.report_id, version=cmd.version,
            outcome=outcome, reason_code=reason_code, detail=detail))

    # ---- reading ------------------------------------------------------------------------------
    def summary(self, conn: Connection, report_id: str) -> ReviewSummary:
        head = conn.execute(select(report_heads.c.current_version)
                            .where(report_heads.c.report_id == report_id)).scalar_one_or_none()
        if head is None:
            raise F6Error("NOT_FOUND", f"Report {report_id} does not exist.", operation_id=f"summary:{report_id}")
        cycles = [_cycle(r) for r in conn.execute(select(review_cycles).where(
            review_cycles.c.report_id == report_id).order_by(review_cycles.c.cycle_number))]
        decisions = [DecisionView(**_decision(r).model_dump(), review_cycle_id=r.review_cycle_id,
                                  memory_sync_status=memory_sync_status(conn, r.decision_id))
                     for r in conn.execute(select(review_decisions).where(
                         review_decisions.c.report_id == report_id).order_by(review_decisions.c.decided_at,
                                                                             review_decisions.c.decision_id))]
        cycle_ids = [c.review_cycle_id for c in cycles]
        intents = [FeedbackIntentView(
            feedback_id=r.feedback_id, decision_id=r.decision_id, review_cycle_id=r.review_cycle_id,
            human_feedback=r.human_feedback, summary_of_human_intent=r.summary_of_human_intent,
            requested_changes=json.loads(r.requested_changes), agent_status=r.agent_status,
            interpreter=r.interpreter, attempts=r.attempts, created_at=r.created_at)
            for r in conn.execute(select(feedback_intents).where(
                feedback_intents.c.review_cycle_id.in_(cycle_ids)).order_by(feedback_intents.c.created_at))]
        reinv = [ReinvestigationView(**dict(r._mapping)) for r in conn.execute(select(reinvestigations).where(
            reinvestigations.c.review_cycle_id.in_(cycle_ids)).order_by(reinvestigations.c.created_at))]
        return ReviewSummary(report_id=report_id, current_version=head, cycles=cycles, decisions=decisions,
                             feedback_intents=intents, reinvestigations=reinv,
                             failed_deliveries=self._failed_deliveries(conn, decisions, intents),
                             max_review_cycles=self.max_review_cycles,
                             cycles_used=self.cycles_used(conn, report_id),
                             limit_reached=any(c.state == "limit_reached" for c in cycles))

    @staticmethod
    def _failed_deliveries(conn, decisions, intents) -> list[FailedDelivery]:
        """Interpretation or re-investigation events the outbox gave up on, mapped to their cycle, so the
        UI can say "delivery failed, an operator can retry" instead of showing work in progress forever."""
        keys = {(INTERPRET_EVENT, d.decision_id): d.review_cycle_id for d in decisions
                if d.decision == "request_changes"}
        keys |= {(REINVESTIGATION_EVENT, request_id_for(i.review_cycle_id, i.feedback_id)): i.review_cycle_id
                 for i in intents}
        if not keys:
            return []
        rows = conn.execute(select(outbox_events).where(
            outbox_events.c.status == "failed",
            outbox_events.c.dedupe_key.in_({dedupe for _, dedupe in keys}))).all()
        return [FailedDelivery(review_cycle_id=keys[(r.event_type, r.dedupe_key)], event_id=r.event_id,
                               event_type=r.event_type, attempts=r.attempts, last_error=r.last_error)
                for r in rows if (r.event_type, r.dedupe_key) in keys]
