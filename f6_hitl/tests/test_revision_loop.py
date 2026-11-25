"""F6-US-03 re-investigation and re-review: targeted follow-up, vN+1 back to review, failures, limits."""
import json

import pytest
from sqlalchemy import text

from observa_hitl.contracts import DecisionCommand
from observa_hitl.errors import F6Error
from observa_hitl.feedback import handle_revision_failed, handle_revision_ready, interpret_handler, \
    reinvestigation_handler
from observa_hitl.gate import ReviewGate
from observa_hitl.interpreter import FakeInterpreter
from observa_hitl.outbox import INTERPRET_EVENT, MEMORY_EVENT, REINVESTIGATION_EVENT, Relay, backoff_seconds
from observa_hitl.projections import review_status
from observa_reports.service import ReportService
from observa_review_service.db import make_engine, unit_of_work
from observa_review_service.mocks.reinvestigator import FakeReinvestigator

from hitl_helpers import bundle

CLEAR = "Re-check the traces for orders-service before blaming payments."


def rows(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).all()


def one(engine, sql, **params):
    return rows(engine, sql, **params)[0][0]


def request_changes(uow, gate, reviewer, record, feedback=CLEAR, key=None):
    return gate.submit(uow, reviewer, DecisionCommand(
        report_id=record.report_id, version=record.version, decision="request_changes", feedback=feedback,
        idempotency_key=key or f"rc-{record.version}-{feedback[:8]}")).decision


def loop_relay(engine, clock, reinvestigator):
    return Relay(engine, {INTERPRET_EVENT: interpret_handler(engine, FakeInterpreter(), clock=clock),
                          REINVESTIGATION_EVENT: reinvestigation_handler(engine, reinvestigator, clock=clock)},
                 max_attempts=8, clock=clock)


def drain(relay, clock, rounds=4):
    for _ in range(rounds):
        relay.run_once()
        clock.advance(400)


def cycle(engine, report_id, version):
    return rows(engine, "SELECT review_cycle_id, cycle_number, state FROM review_cycles "
                        "WHERE report_id=:r AND report_version=:v", r=report_id, v=version)[0]


def revise(engine, clock, gate, record, review_cycle_id, name="payments_v2_revised"):
    with unit_of_work(engine) as conn:
        return handle_revision_ready(conn, record.report_id, bundle(name), review_cycle_id, gate=gate,
                                     reports=ReportService(clock=clock), clock=clock)


@pytest.fixture()
def f2():
    return FakeReinvestigator()


def test_targeted_followup_built_from_intent(engine, uow, clock, gate, publish, alice, f2):
    record = publish("payments_v1")
    request_changes(uow, gate, alice, record, feedback="Re-check the metrics before deciding.")
    drain(loop_relay(engine, clock, f2), clock)
    [(follow_up, intent)] = f2.requests.values()
    assert follow_up.target_agents == ["metrics"]
    assert follow_up.origin == "human_feedback" and follow_up.status == "pending"
    assert follow_up.review_cycle_id == cycle(engine, record.report_id, 1).review_cycle_id
    assert follow_up.source_run_id == record.source_investigation_run_id
    assert follow_up.objectives and set(follow_up.signal_objectives) == {"metrics"}
    assert follow_up.evidence_needed and all(e.startswith("EV-") or e for e in follow_up.evidence_needed)
    assert intent.human_feedback == "Re-check the metrics before deciding."
    [(status, f2_run)] = rows(engine, "SELECT status, f2_run_id FROM reinvestigations")
    assert status == "requested" and f2_run is None


def test_duplicate_dispatch_one_request(engine, uow, clock, gate, publish, alice):
    record = publish("payments_v1")
    request_changes(uow, gate, alice, record)
    f2 = FakeReinvestigator(crash_after_request=True)  # F2 accepted it, then the worker died before the ack
    relay = loop_relay(engine, clock, f2)
    drain(relay, clock, rounds=5)
    assert f2.calls == 2 and len(f2.requests) == 1
    assert one(engine, "SELECT count(*) FROM reinvestigations") == 1
    assert one(engine, "SELECT status FROM outbox_events WHERE event_type=:t", t=REINVESTIGATION_EVENT) == "delivered"


def test_reinvestigator_outage_is_retried_by_the_outbox(engine, uow, clock, gate, publish, alice):
    record = publish("payments_v1")
    request_changes(uow, gate, alice, record)
    f2 = FakeReinvestigator(fail_times=2)
    relay = loop_relay(engine, clock, f2)
    relay.run_once()  # interpret
    relay.run_once()  # dispatch fails
    assert one(engine, "SELECT status FROM outbox_events WHERE event_type=:t", t=REINVESTIGATION_EVENT) == "pending"
    for n in (1, 2):
        clock.advance(backoff_seconds(n))
        relay.run_once()
    assert len(f2.requests) == 1 and f2.calls == 3


def test_revision_creates_v2_and_returns_to_pending_review(engine, uow, clock, gate, publish, alice, f2):
    v1 = publish("payments_v1")
    request_changes(uow, gate, alice, v1)
    drain(loop_relay(engine, clock, f2), clock)
    first = cycle(engine, v1.report_id, 1)
    assert first.state == "reinvestigating"
    v2 = revise(engine, clock, gate, v1, first.review_cycle_id)
    assert v2.version == 2 and v2.previous_version == 1 and v2.review_cycle_id == first.review_cycle_id
    assert cycle(engine, v1.report_id, 1).state == "superseded"
    second = cycle(engine, v1.report_id, 2)
    assert (second.cycle_number, second.state) == (2, "pending_review")
    with uow() as conn:  # never approved automatically
        assert review_status(conn, v1.report_id, 2) == "pending_review"
    assert one(engine, "SELECT count(*) FROM outbox_events WHERE event_type=:t", t=MEMORY_EVENT) == 0
    assert rows(engine, "SELECT status, f2_run_id FROM reinvestigations") == \
        [("completed", v2.source_investigation_run_id)]
    # The new version is reviewed like any other.
    assert gate.submit(uow, alice, DecisionCommand(report_id=v1.report_id, version=2, decision="approve",
                                                   idempotency_key="approve-v2")).decision.version == 2


def test_revision_ready_is_idempotent_for_a_redelivered_result(engine, uow, clock, gate, publish, alice, f2):
    v1 = publish("payments_v1")
    request_changes(uow, gate, alice, v1)
    drain(loop_relay(engine, clock, f2), clock)
    first = cycle(engine, v1.report_id, 1)
    v2 = revise(engine, clock, gate, v1, first.review_cycle_id)
    again = revise(engine, clock, gate, v1, first.review_cycle_id)  # F2 delivered the same result twice
    assert again == v2
    assert one(engine, "SELECT count(*) FROM report_versions") == 2
    assert one(engine, "SELECT count(*) FROM review_cycles") == 2


def test_revision_failure_keeps_v1_current_and_visible(engine, uow, clock, gate, publish, alice, f2):
    v1 = publish("payments_v1")
    request_changes(uow, gate, alice, v1)
    drain(loop_relay(engine, clock, f2), clock)
    [request_id] = f2.requests
    with unit_of_work(engine) as conn:
        handle_revision_failed(conn, request_id, "F2 follow-up timed out", clock=clock)
    assert cycle(engine, v1.report_id, 1).state == "revision_failed"
    assert rows(engine, "SELECT status, last_error FROM reinvestigations") == [("failed", "F2 follow-up timed out")]
    assert one(engine, "SELECT current_version FROM report_heads") == 1
    with uow() as conn:
        summary = gate.summary(conn, v1.report_id)
        assert review_status(conn, v1.report_id, 1) == "changes_requested"  # not approved
    assert summary.cycles[0].state == "revision_failed" and summary.current_version == 1
    assert summary.reinvestigations[0].status == "failed"
    # The loop stopped: no new change request on this cycle, but the reviewer may still approve v1 explicitly.
    with pytest.raises(F6Error) as exc:
        request_changes(uow, gate, alice, v1, feedback="Try again please.", key="again")
    assert exc.value.envelope.code == "VERSION_CONFLICT"
    assert gate.submit(uow, alice, DecisionCommand(report_id=v1.report_id, version=1, decision="approve",
                                                   idempotency_key="approve-anyway")).decision.decision == "approve"
    with pytest.raises(F6Error):  # a late success for the failed request is refused
        revise(engine, clock, gate, v1, cycle(engine, v1.report_id, 1).review_cycle_id)


def test_limit_reached_stops_safely_without_approval(engine, uow, clock, publish, alice, f2):
    gate = ReviewGate(clock=clock, max_review_cycles=2)
    v1 = publish("payments_v1")
    request_changes(uow, gate, alice, v1)
    drain(loop_relay(engine, clock, f2), clock)
    v2 = revise(engine, clock, gate, v1, cycle(engine, v1.report_id, 1).review_cycle_id)
    decision = request_changes(uow, gate, alice, v2, feedback="Still wrong: check the logs.")
    assert decision.decision == "request_changes"  # the reviewer's decision is recorded
    assert cycle(engine, v1.report_id, 2).state == "limit_reached"
    assert one(engine, "SELECT count(*) FROM outbox_events WHERE dedupe_key=:d", d=decision.decision_id) == 0
    drain(loop_relay(engine, clock, f2), clock)
    assert len(f2.requests) == 1 and one(engine, "SELECT count(*) FROM review_cycles") == 2
    with uow() as conn:
        assert review_status(conn, v1.report_id, 2) == "changes_requested"
        summary = gate.summary(conn, v1.report_id)
    assert summary.limit_reached and summary.cycles_used == 2 and summary.max_review_cycles == 2
    assert one(engine, "SELECT count(*) FROM outbox_events WHERE event_type=:t", t=MEMORY_EVENT) == 0
    with pytest.raises(F6Error) as exc:
        request_changes(uow, gate, alice, v2, feedback="One more round.", key="one-more")
    assert exc.value.envelope.code == "RETRY_LIMIT_REACHED"


def test_approve_is_still_allowed_on_the_last_cycle(engine, uow, clock, publish, alice):
    gate = ReviewGate(clock=clock, max_review_cycles=1)
    v1 = publish("payments_v1")
    assert gate.submit(uow, alice, DecisionCommand(report_id=v1.report_id, version=1, decision="approve",
                                                   idempotency_key="a")).decision.decision == "approve"


def test_restart_mid_loop_preserves_consumed_limit(db_url, engine, uow, clock, publish, alice, f2):
    gate = ReviewGate(clock=clock, max_review_cycles=2)
    v1 = publish("payments_v1")
    request_changes(uow, gate, alice, v1)
    drain(loop_relay(engine, clock, f2), clock)
    v2 = revise(engine, clock, gate, v1, cycle(engine, v1.report_id, 1).review_cycle_id)
    engine.dispose()  # restart: a fresh engine and gate know only what the database says
    restarted = make_engine(db_url)
    try:
        fresh = ReviewGate(clock=clock, max_review_cycles=2)
        fresh.submit(lambda: unit_of_work(restarted), alice, DecisionCommand(
            report_id=v2.report_id, version=2, decision="request_changes", feedback="Check logs again.",
            idempotency_key="after-restart"))
        assert cycle(restarted, v1.report_id, 2).state == "limit_reached"
    finally:
        restarted.dispose()


def test_revision_for_wrong_cycle_conflicts(engine, uow, clock, gate, publish, alice, f2):
    v1 = publish("payments_v1")
    pending = cycle(engine, v1.report_id, 1)
    with pytest.raises(F6Error) as exc:  # the cycle never asked for a revision
        revise(engine, clock, gate, v1, pending.review_cycle_id)
    assert exc.value.envelope.code == "VERSION_CONFLICT"
    with pytest.raises(F6Error) as exc:
        revise(engine, clock, gate, v1, "CYCLE-unknown")
    assert exc.value.envelope.code == "NOT_FOUND"
    other = publish("db_exhaustion")
    request_changes(uow, gate, alice, other, feedback="Re-check the logs for orders-service.")
    drain(loop_relay(engine, clock, f2), clock)
    with pytest.raises(F6Error) as exc:  # a cycle of another report
        revise(engine, clock, gate, v1, cycle(engine, other.report_id, 1).review_cycle_id)
    assert exc.value.envelope.code == "VERSION_CONFLICT"
    assert one(engine, "SELECT count(*) FROM report_versions WHERE report_id=:r", r=v1.report_id) == 1


def test_new_upstream_version_supersedes_open_cycles_but_not_decided_ones(engine, uow, clock, gate, publish, alice):
    v1 = publish("payments_v1")
    publish("payments_v2_revised", expected_previous=1)  # an upstream update, no F6 cycle asked for it
    assert cycle(engine, v1.report_id, 1).state == "superseded"
    assert cycle(engine, v1.report_id, 2).state == "pending_review"
    gate.submit(uow, alice, DecisionCommand(report_id=v1.report_id, version=2, decision="approve",
                                            idempotency_key="ok"))
    with uow() as conn:  # opening is idempotent and never rewrites a decided cycle
        gate.open(conn, v1.report_id, 2)
    assert cycle(engine, v1.report_id, 2).state == "approved"


def test_reinvestigation_skips_a_cycle_that_moved_on(engine, uow, clock, gate, publish, alice, f2):
    v1 = publish("payments_v1")
    request_changes(uow, gate, alice, v1)
    relay = loop_relay(engine, clock, f2)
    relay.run_once()  # interpreted -> reinvestigating, dispatch pending
    publish("payments_v2_revised", expected_previous=1)  # an upstream v2 supersedes the cycle meanwhile
    relay.run_once()
    assert f2.requests == {} and one(engine, "SELECT count(*) FROM reinvestigations") == 0
    assert one(engine, "SELECT status FROM outbox_events WHERE event_type=:t", t=REINVESTIGATION_EVENT) == "delivered"


def test_history_links_feedback_decisions_and_versions(engine, uow, clock, gate, publish, alice, f2):
    v1 = publish("payments_v1")
    decision = request_changes(uow, gate, alice, v1)
    drain(loop_relay(engine, clock, f2), clock)
    first = cycle(engine, v1.report_id, 1)
    revise(engine, clock, gate, v1, first.review_cycle_id)
    with uow() as conn:
        summary = gate.summary(conn, v1.report_id)
        history = ReportService().history(conn, v1.report_id)
    assert [(c.report_version, c.cycle_number, c.state) for c in summary.cycles] == \
        [(1, 1, "superseded"), (2, 2, "pending_review")]
    [d] = summary.decisions
    assert (d.decision_id, d.review_cycle_id, d.version) == (decision.decision_id, first.review_cycle_id, 1)
    [intent] = summary.feedback_intents
    assert intent.decision_id == decision.decision_id and intent.human_feedback == CLEAR
    [reinv] = summary.reinvestigations
    assert (reinv.review_cycle_id, reinv.feedback_id, reinv.status) == \
        (first.review_cycle_id, intent.feedback_id, "completed")
    assert [(h.version, h.review_cycle_id) for h in history] == [(1, None), (2, first.review_cycle_id)]
    assert json.loads(json.dumps(summary.model_dump(mode="json")))  # serializable for the API


def test_an_approved_older_version_stays_approved_when_a_newer_one_is_published(engine, uow, gate, publish, alice):
    v1 = publish("payments_v1")
    gate.submit(uow, alice, DecisionCommand(report_id=v1.report_id, version=1, decision="approve",
                                            idempotency_key="approve-v1"))
    publish("payments_v2_revised", expected_previous=1)
    assert cycle(engine, v1.report_id, 1).state == "approved"  # history is never rewritten
    assert cycle(engine, v1.report_id, 2).state == "pending_review"
    with uow() as conn:
        assert review_status(conn, v1.report_id, 1) == "approved"
