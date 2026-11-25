"""F6-US-01 review gate: explicit decisions, exact versions, idempotency, conflicts, audit, restart."""
import json
import threading

import pytest
from sqlalchemy import text

from observa_hitl.contracts import DecisionCommand
from observa_hitl.errors import F6Error
from observa_hitl.gate import ReviewGate
from observa_hitl.projections import memory_sync_status, review_status
from observa_review_service.db import make_engine, unit_of_work

from hitl_helpers import CLOCK


def approve(record, key="key-1", feedback=None):
    return DecisionCommand(report_id=record.report_id, version=record.version, decision="approve",
                           feedback=feedback, idempotency_key=key)


def changes(record, feedback="Check the payment provider before blaming payments-service.", key="key-2"):
    return DecisionCommand(report_id=record.report_id, version=record.version, decision="request_changes",
                           feedback=feedback, idempotency_key=key)


def decide(uow, gate, reviewer, cmd):
    with uow() as conn:
        return gate.decide(conn, reviewer, cmd)


def outbox(engine):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(
            "SELECT event_id, event_type, dedupe_key, payload, status FROM outbox_events ORDER BY created_at"))]


def code(excinfo):
    return excinfo.value.envelope.code


def test_report_stays_pending_until_explicit_decision(engine, uow, gate, publish, count):
    record = publish("payments_v1")
    with uow() as conn:
        assert review_status(conn, record.report_id, 1) == "pending_review"
        again = gate.open(conn, record.report_id, 1)  # opening is idempotent
        summary = gate.summary(conn, record.report_id)
    assert again.state == "pending_review" and again.cycle_number == 1
    assert [c.state for c in summary.cycles] == ["pending_review"]
    assert summary.decisions == [] and summary.current_version == 1
    assert count("review_decisions") == 0 and count("outbox_events") == 0


def test_approve_records_exact_version_and_enqueues_memory_event(engine, uow, gate, publish, alice):
    record = publish("payments_v1")
    outcome = decide(uow, gate, alice, approve(record, feedback="  looks good  "))
    d = outcome.decision
    assert not outcome.replayed
    assert (d.report_id, d.version, d.incident_id, d.reviewer_id, d.decision) == \
        (record.report_id, 1, record.incident_id, "rev-alice", "approve")
    assert d.feedback is None  # stored as null for approve (contract)
    assert d.decided_at == CLOCK and d.decision_id.startswith("DEC-") and d.idempotency_key == "key-1"
    [event] = outbox(engine)
    assert event["event_type"] == "memory.approved_report.v1" and event["dedupe_key"] == d.decision_id
    payload = json.loads(event["payload"])
    assert payload["decision_id"] == d.decision_id and payload["version"] == 1
    assert payload["content_digest"] == record.content_digest and payload["evidence_ids"] == record.evidence_ids
    assert payload["reviewer_id"] == "rev-alice" and payload["report_id"] == record.report_id
    with uow() as conn:
        assert review_status(conn, record.report_id, 1) == "approved"
        assert memory_sync_status(conn, d.decision_id) == "pending"
        assert [c.state for c in gate.summary(conn, record.report_id).cycles] == ["approved"]


@pytest.mark.parametrize("feedback", [None, "", "   \n\t "])
def test_request_changes_requires_nonempty_feedback(engine, uow, gate, publish, alice, count, feedback):
    record = publish("payments_v1")
    with pytest.raises(F6Error) as exc:
        decide(uow, gate, alice, changes(record, feedback=feedback))
    assert code(exc) == "VALIDATION_ERROR"
    assert count("review_decisions") == 0 and count("outbox_events") == 0
    with uow() as conn:
        assert review_status(conn, record.report_id, 1) == "pending_review"


def test_request_changes_never_enqueues_memory_event(engine, uow, gate, publish, alice):
    record = publish("payments_v1")
    outcome = decide(uow, gate, alice, changes(record, feedback="  Re-check traces for payments-service.  "))
    assert outcome.decision.feedback == "Re-check traces for payments-service."  # kept, only trimmed
    assert [e["event_type"] for e in outbox(engine)] == ["feedback.interpret.v1"]
    with uow() as conn:
        assert review_status(conn, record.report_id, 1) == "changes_requested"
        assert memory_sync_status(conn, outcome.decision.decision_id) == "not_requested"
        assert gate.summary(conn, record.report_id).cycles[0].state == "changes_requested"


def test_same_key_same_payload_replays_once(engine, uow, gate, publish, alice, count):
    record = publish("payments_v1")
    first = decide(uow, gate, alice, approve(record))
    second = decide(uow, gate, alice, approve(record))
    assert not first.replayed and second.replayed
    assert second.decision == first.decision
    assert count("review_decisions") == 1 and count("outbox_events") == 1
    assert count("review_audit", "outcome = 'replayed'") == 1


def test_same_key_different_payload_conflicts(engine, uow, gate, publish, alice, bob, count):
    record = publish("payments_v1")
    decide(uow, gate, alice, changes(record, key="k"))
    with pytest.raises(F6Error) as exc:
        decide(uow, gate, alice, changes(record, feedback="Something else entirely.", key="k"))
    assert code(exc) == "IDEMPOTENCY_CONFLICT"
    with pytest.raises(F6Error) as exc:  # the same key from another reviewer is another request
        decide(uow, gate, bob, changes(record, key="k"))
    assert code(exc) == "IDEMPOTENCY_CONFLICT"
    assert count("review_decisions") == 1 and count("outbox_events") == 1


def test_stale_version_conflicts_after_v2(engine, uow, gate, publish, alice, count):
    v1 = publish("payments_v1")
    v2 = publish("payments_v2_revised", expected_previous=1)
    assert v2.version == 2
    with pytest.raises(F6Error) as exc:
        decide(uow, gate, alice, approve(v1, key="old"))
    assert code(exc) == "VERSION_CONFLICT"
    assert exc.value.envelope.details["current_version"] == 2
    assert decide(uow, gate, alice, approve(v2, key="new")).decision.version == 2
    assert count("review_decisions") == 1


def test_decided_cycle_rejects_a_second_final_decision(engine, uow, gate, publish, alice, bob):
    record = publish("payments_v1")
    decide(uow, gate, alice, approve(record, key="a"))
    with pytest.raises(F6Error) as exc:
        decide(uow, gate, bob, changes(record, key="b"))
    assert code(exc) == "VERSION_CONFLICT" and exc.value.envelope.details["state"] == "approved"


def test_competing_reviewers_one_wins(engine, gate, publish, alice, bob, count):
    record = publish("payments_v1")
    barrier, results = threading.Barrier(2), {}

    def run(name, reviewer, cmd):
        barrier.wait()
        try:
            results[name] = gate.submit(lambda: unit_of_work(engine), reviewer, cmd)
        except F6Error as exc:
            results[name] = exc

    threads = [threading.Thread(target=run, args=("alice", alice, approve(record, key="a"))),
               threading.Thread(target=run, args=("bob", bob, changes(record, key="b")))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    errors = [r for r in results.values() if isinstance(r, F6Error)]
    wins = [r for r in results.values() if not isinstance(r, F6Error)]
    assert len(wins) == 1 and len(errors) == 1
    assert errors[0].envelope.code == "VERSION_CONFLICT"
    assert count("review_decisions") == 1 and count("outbox_events") == 1
    assert count("review_audit", "outcome = 'rejected' AND reason_code = 'VERSION_CONFLICT'") == 1


def test_unauthorized_attempt_rejected_and_audited(engine, uow, gate, publish, vic, count):
    record = publish("payments_v1")
    with pytest.raises(F6Error) as exc:
        gate.submit(uow, vic, approve(record))
    assert code(exc) == "NOT_AUTHORIZED"
    # The decision transaction rolled back; the rejection was recorded in its own transaction.
    assert count("review_decisions") == 0 and count("outbox_events") == 0
    with engine.connect() as conn:
        row = conn.execute(text("SELECT actor, action, report_id, version, outcome, reason_code FROM review_audit"))\
            .one()
    assert tuple(row) == ("view-vic", "decide", record.report_id, 1, "rejected", "NOT_AUTHORIZED")
    with uow() as conn:
        assert review_status(conn, record.report_id, 1) == "pending_review"


def test_accepted_decision_is_audited_in_the_same_transaction(engine, uow, gate, publish, alice, count):
    record = publish("payments_v1")
    gate.submit(uow, alice, approve(record))
    assert count("review_audit", "outcome = 'accepted' AND actor = 'rev-alice'") == 1


def test_restart_keeps_pending_review(db_url, engine, gate, publish, alice):
    record = publish("payments_v1")
    engine.dispose()  # the process stops
    restarted = make_engine(db_url)
    try:
        with unit_of_work(restarted) as conn:
            assert review_status(conn, record.report_id, 1) == "pending_review"
            outcome = ReviewGate().decide(conn, alice, approve(record))
        assert outcome.decision.version == 1
        with unit_of_work(restarted) as conn:
            assert review_status(conn, record.report_id, 1) == "approved"
    finally:
        restarted.dispose()


def test_version_published_before_f6_is_opened_on_first_decision(engine, uow, gate, clock, alice):
    from observa_reports.service import ReportService
    from hitl_helpers import bundle
    with uow() as conn:
        record = ReportService(clock=clock).publish(conn, bundle("db_exhaustion"))  # no gate.open
    outcome = decide(uow, gate, alice, approve(record))
    assert outcome.decision.version == 1


def test_unknown_report_or_version_not_found(uow, gate, publish, alice):
    record = publish("payments_v1")
    with pytest.raises(F6Error) as exc:
        decide(uow, gate, alice, DecisionCommand(report_id="RPT-nope", version=1, decision="approve",
                                                 feedback=None, idempotency_key="x"))
    assert code(exc) == "NOT_FOUND"
    with pytest.raises(F6Error) as exc:
        decide(uow, gate, alice, approve(record.model_copy(update={"version": 7}), key="y"))
    assert code(exc) == "VERSION_CONFLICT"


def test_clarification_needed_cycle_accepts_a_new_decision(engine, uow, gate, publish, alice):
    record = publish("payments_v1")
    decide(uow, gate, alice, changes(record, key="first"))
    with uow() as conn:  # what F6-US-02 does for unclear feedback (Task 5.2)
        cycle = gate.summary(conn, record.report_id).cycles[0]
        conn.execute(text("UPDATE review_cycles SET state='clarification_needed', state_version=state_version+1 "
                          "WHERE review_cycle_id=:c"), {"c": cycle.review_cycle_id})
    assert decide(uow, gate, alice, approve(record, key="second")).decision.decision == "approve"


def test_decisions_are_immutable(engine, uow, gate, publish, alice):
    record = publish("payments_v1")
    decide(uow, gate, alice, approve(record))
    with pytest.raises(Exception, match="immutable"):
        with uow() as conn:
            conn.execute(text("UPDATE review_decisions SET reviewer_id='someone-else'"))


def test_deterministic_ids(engine, uow, gate, publish, alice):
    import hashlib
    record = publish("payments_v1")
    d = decide(uow, gate, alice, approve(record, key="k-42")).decision
    expected = "DEC-" + hashlib.sha256(f"{record.report_id}|1|k-42".encode()).hexdigest()[:16]
    cycle = "CYCLE-" + hashlib.sha256(f"{record.report_id}|1".encode()).hexdigest()[:16]
    assert d.decision_id == expected
    with uow() as conn:
        assert gate.summary(conn, record.report_id).cycles[0].review_cycle_id == cycle


def test_cycle_cas_rejects_a_cycle_changed_after_it_was_read(engine, uow, gate, publish, alice, count):
    """Postgres READ COMMITTED lets another writer change the cycle between our read and our
    UPDATE; SQLite's BEGIN IMMEDIATE never does, so the race is simulated on the same connection."""
    from sqlalchemy import event
    record = publish("payments_v1")
    fired = []

    def rival(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("UPDATE REVIEW_CYCLES") and not fired:
            fired.append(True)
            cursor.execute("UPDATE review_cycles SET state_version = state_version + 1")

    with pytest.raises(F6Error) as exc:
        with uow() as conn:
            event.listen(conn, "before_cursor_execute", rival)
            gate.decide(conn, alice, approve(record))
    assert fired and code(exc) == "VERSION_CONFLICT"
    assert count("review_decisions") == 0 and count("outbox_events") == 0
