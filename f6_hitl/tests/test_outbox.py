"""F6-US-01 crash-safe approval handoff: transactional outbox, bounded retries, idempotent F5 delivery."""
import json

import pytest
from sqlalchemy import text

from observa_hitl.contracts import DecisionCommand
from observa_hitl.errors import F6Error
from observa_hitl.outbox import MEMORY_EVENT, Outbox, Relay, backoff_seconds, memory_handler
from observa_hitl.projections import memory_sync_status, review_status
from observa_review_service.mocks.memory_sink import FakeMemorySink


def approve(record, key="key-1"):
    return DecisionCommand(report_id=record.report_id, version=record.version, decision="approve",
                           feedback=None, idempotency_key=key)


def changes(record, key="key-2"):
    return DecisionCommand(report_id=record.report_id, version=record.version, decision="request_changes",
                           feedback="Re-check the traces.", idempotency_key=key)


@pytest.fixture()
def approved(uow, gate, publish, alice):
    record = publish("payments_v1")
    return gate.submit(uow, alice, approve(record)).decision


def relay_for(engine, clock, sink, max_attempts=8, **handlers):
    return Relay(engine, {MEMORY_EVENT: memory_handler(sink), **handlers}, max_attempts=max_attempts, clock=clock,
                 lease_seconds=60)


def event_row(engine, dedupe_key):
    with engine.connect() as conn:
        return conn.execute(text("SELECT * FROM outbox_events WHERE dedupe_key = :k"), {"k": dedupe_key}).one()


def sync(uow, decision_id):
    with uow() as conn:
        return memory_sync_status(conn, decision_id)


def test_approved_event_delivered_once_memory_sync_stored(engine, uow, clock, approved):
    sink = FakeMemorySink()
    relay = relay_for(engine, clock, sink)
    assert sync(uow, approved.decision_id) == "pending"
    assert relay.run_once() == 1
    assert relay.run_once() == 0  # nothing left to deliver
    assert list(sink.records) == [approved.decision_id] and sink.calls == 1
    stored = sink.records[approved.decision_id]
    assert (stored.report_id, stored.version, stored.reviewer_id) == (approved.report_id, 1, "rev-alice")
    assert sync(uow, approved.decision_id) == "stored"
    row = event_row(engine, approved.decision_id)
    assert row.status == "delivered" and row.attempts == 1 and row.claimed_until is None


def test_sink_outage_retries_then_stored(engine, uow, clock, approved):
    sink = FakeMemorySink(fail_times=2)
    relay = relay_for(engine, clock, sink)
    relay.run_once()
    row = event_row(engine, approved.decision_id)
    assert row.status == "pending" and row.attempts == 1 and "outage" in row.last_error
    assert relay.run_once() == 0  # backing off: not due yet
    clock.advance(backoff_seconds(1))
    relay.run_once()
    assert sync(uow, approved.decision_id) == "pending"
    clock.advance(backoff_seconds(2))
    relay.run_once()
    assert sync(uow, approved.decision_id) == "stored"
    assert sink.calls == 3 and len(sink.records) == 1


def test_backoff_is_exponential_and_capped():
    assert [backoff_seconds(n) for n in (0, 1, 2, 3, 6, 7, 20)] == [5, 10, 20, 40, 300, 300, 300]


def test_permanent_outage_marks_failed_not_stored(engine, uow, clock, approved, publish):
    sink = FakeMemorySink(fail_forever=True)
    relay = relay_for(engine, clock, sink, max_attempts=3)
    for _ in range(5):
        relay.run_once()
        clock.advance(400)
    row = event_row(engine, approved.decision_id)
    assert row.status == "failed" and row.attempts == 3 and sink.calls == 3
    assert sync(uow, approved.decision_id) == "failed"
    with uow() as conn:  # the approval itself stands: sync is separate from review status
        assert review_status(conn, approved.report_id, 1) == "approved"
    with uow() as conn:
        Outbox(clock=clock).retry(conn, row.event_id)
    assert sync(uow, approved.decision_id) == "pending" and event_row(engine, approved.decision_id).attempts == 0
    sink.fail_forever = False
    relay.run_once()
    assert sync(uow, approved.decision_id) == "stored" and len(sink.records) == 1


def test_retry_only_requeues_failed_events(engine, uow, clock, approved):
    row = event_row(engine, approved.decision_id)
    with pytest.raises(F6Error) as exc:
        with uow() as conn:
            Outbox(clock=clock).retry(conn, row.event_id)
    assert exc.value.envelope.code == "VERSION_CONFLICT"
    with pytest.raises(F6Error) as exc:
        with uow() as conn:
            Outbox(clock=clock).retry(conn, "EVT-missing")
    assert exc.value.envelope.code == "NOT_FOUND"


def test_crash_after_store_before_ack_redelivers_idempotently(engine, uow, clock, approved):
    sink = FakeMemorySink(crash_after_store=True)
    relay = relay_for(engine, clock, sink)
    relay.run_once()  # F5 stored the record, then the delivery failed before the ack
    assert sync(uow, approved.decision_id) == "pending" and len(sink.records) == 1
    clock.advance(backoff_seconds(1))
    relay.run_once()
    assert sync(uow, approved.decision_id) == "stored"
    assert sink.calls == 2 and len(sink.records) == 1  # redelivered, still exactly one F5 record


def test_lease_expiry_allows_reclaim(engine, uow, clock, approved):
    """A worker that dies mid-delivery (no outcome recorded) holds a lease; another worker
    reclaims the event only after the lease expires."""
    sink = FakeMemorySink()

    def dies(payload):
        raise SystemExit("worker killed")

    with pytest.raises(SystemExit):
        Relay(engine, {MEMORY_EVENT: dies}, max_attempts=8, clock=clock, lease_seconds=60).run_once()
    row = event_row(engine, approved.decision_id)
    assert row.status == "pending" and row.claimed_until is not None and row.attempts == 1
    relay = relay_for(engine, clock, sink)
    assert relay.run_once() == 0  # still leased by the dead worker
    clock.advance(61)
    assert relay.run_once() == 1
    assert sync(uow, approved.decision_id) == "stored" and event_row(engine, approved.decision_id).attempts == 2


def test_stale_worker_cannot_ack_after_its_lease_was_reclaimed(engine, uow, clock, approved):
    """Worker A stalls past its lease; worker B reclaims and delivers. A's late outcome
    (and the database step its handler returned) must be discarded."""
    sink, applied = FakeMemorySink(), []

    def slow(payload):
        sink.store_payload(payload)
        clock.advance(61)
        assert relay_for(engine, clock, sink).run_once() == 1  # B takes over meanwhile
        return lambda conn: applied.append("A")

    Relay(engine, {MEMORY_EVENT: slow}, max_attempts=8, clock=clock, lease_seconds=60).run_once()
    row = event_row(engine, approved.decision_id)
    assert row.status == "delivered" and row.attempts == 2
    assert applied == [] and len(sink.records) == 1


def test_handler_database_step_commits_with_the_ack(engine, uow, clock, approved):
    def handler(payload):
        def step(conn):
            conn.execute(text("INSERT INTO review_audit (at, action, outcome) VALUES ('2026-01-01T00:00:00Z', "
                              "'probe', 'accepted')"))
            raise RuntimeError("database step failed")
        return step

    Relay(engine, {MEMORY_EVENT: handler}, max_attempts=8, clock=clock).run_once()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM review_audit WHERE action='probe'")).scalar_one() == 0
    row = event_row(engine, approved.decision_id)
    assert row.status == "pending" and "database step failed" in row.last_error


def test_only_approved_versions_reach_memory(engine, uow, clock, gate, publish, alice):
    changed = publish("payments_v1")
    gate.submit(uow, alice, changes(changed))
    publish("db_exhaustion")  # pending, never decided
    sink = FakeMemorySink()
    relay = relay_for(engine, clock, sink)
    assert relay.run_once() == 0
    assert sink.calls == 0 and sink.records == {}
    with engine.connect() as conn:  # the change request waits for its own handler (Task 5.2)
        rows = conn.execute(text("SELECT event_type, status, attempts FROM outbox_events")).all()
    assert [tuple(r) for r in rows] == [("feedback.interpret.v1", "pending", 0)]


def test_enqueue_is_idempotent_on_type_and_dedupe_key(engine, uow, clock):
    outbox = Outbox(clock=clock)
    with uow() as conn:
        first = outbox.enqueue(conn, MEMORY_EVENT, "DEC-x", {"a": 1})
        second = outbox.enqueue(conn, MEMORY_EVENT, "DEC-x", {"a": 2})
    assert first == second
    with engine.connect() as conn:
        assert json.loads(conn.execute(text("SELECT payload FROM outbox_events")).scalar_one()) == {"a": 1}
