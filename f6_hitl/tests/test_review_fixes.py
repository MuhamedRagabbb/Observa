"""Regressions for the whole-branch review findings (F6 side)."""
import httpx
import pytest
from sqlalchemy import event, text

from observa_hitl.contracts import DecisionCommand
from observa_hitl.errors import F6Error
from observa_hitl.feedback import handle_revision_ready, interpret_handler, reinvestigation_handler
from observa_hitl.gate import ReviewGate
from observa_hitl.interpreter import FakeInterpreter, allowed_targets
from observa_hitl.interpreter.groq import GroqInterpreter, ProviderUnavailable
from observa_hitl.outbox import INTERPRET_EVENT, REINVESTIGATION_EVENT, Relay
from observa_reports.assemble import assemble
from observa_reports.service import ReportService
from observa_review_service.db import unit_of_work
from observa_review_service.mocks.reinvestigator import FakeReinvestigator

from hitl_helpers import bundle

CLEAR = "Re-check the traces for orders-service before blaming payments."


def rows(engine, sql, **params):
    with engine.connect() as conn:
        return conn.execute(text(sql), params).all()


def request_changes(uow, gate, reviewer, record, feedback=CLEAR, key="rc"):
    return gate.submit(uow, reviewer, DecisionCommand(report_id=record.report_id, version=record.version,
                                                      decision="request_changes", feedback=feedback,
                                                      idempotency_key=key)).decision


def to_reinvestigating(engine, clock, uow, gate, reviewer, record):
    request_changes(uow, gate, reviewer, record)
    relay = Relay(engine, {INTERPRET_EVENT: interpret_handler(engine, FakeInterpreter(), clock=clock),
                           REINVESTIGATION_EVENT: reinvestigation_handler(engine, FakeReinvestigator(), clock=clock)},
                  clock=clock)
    relay.run_once()
    relay.run_once()
    return rows(engine, "SELECT review_cycle_id FROM review_cycles WHERE report_version=:v", v=record.version)[0][0]


# ---- Finding 2: a revision that does not change the report must not dead-end the cycle --------------

def test_revision_identical_to_the_current_version_is_rejected_and_the_cycle_stays_open(
        engine, uow, clock, gate, publish, alice):
    v1 = publish("payments_v1")
    cycle_id = to_reinvestigating(engine, clock, uow, gate, alice, v1)
    with pytest.raises(F6Error) as exc:
        with unit_of_work(engine) as conn:
            handle_revision_ready(conn, v1.report_id, bundle("payments_v1"), cycle_id, gate=gate,
                                  reports=ReportService(clock=clock), clock=clock)
    assert exc.value.envelope.code == "VALIDATION_ERROR"
    assert rows(engine, "SELECT state FROM review_cycles") == [("reinvestigating",)]
    assert rows(engine, "SELECT current_version FROM report_heads") == [(1,)]
    # F2 can still deliver a real revision afterwards.
    with unit_of_work(engine) as conn:
        v2 = handle_revision_ready(conn, v1.report_id, bundle("payments_v2_revised"), cycle_id, gate=gate,
                                   reports=ReportService(clock=clock), clock=clock)
    assert v2.version == 2


# ---- Finding 3: lock the report head BEFORE the idempotency lookup (Postgres READ COMMITTED) ---------

def test_decide_locks_the_head_before_looking_up_the_idempotency_key(engine, uow, gate, publish, alice):
    record = publish("payments_v1")
    seen = []

    def record_sql(conn, cursor, statement, parameters, context, executemany):
        flat = " ".join(statement.split()).lower()
        if "from report_heads" in flat:
            seen.append("head")
        elif "from review_decisions" in flat and "idempotency_key" in flat:
            seen.append("idempotency")

    with uow() as conn:
        event.listen(conn, "before_cursor_execute", record_sql)
        gate.decide(conn, alice, DecisionCommand(report_id=record.report_id, version=1, decision="approve",
                                                 idempotency_key="k"))
    assert seen.index("head") < seen.index("idempotency")


def test_a_replay_on_a_version_that_became_stale_still_replays(engine, uow, gate, publish, alice):
    v1 = publish("payments_v1")
    first = gate.submit(uow, alice, DecisionCommand(report_id=v1.report_id, version=1, decision="approve",
                                                    idempotency_key="k"))
    publish("payments_v2_revised", expected_previous=1)
    again = gate.submit(uow, alice, DecisionCommand(report_id=v1.report_id, version=1, decision="approve",
                                                    idempotency_key="k"))
    assert again.replayed and again.decision == first.decision


# ---- Finding 4: deterministic provider rejections and terminal deliveries are visible -----------------

def _groq(status, body):
    def answer(request):
        return httpx.Response(status, json=body)
    return GroqInterpreter.from_settings(api_key="k", base_url="http://groq.stub/v1", model="m",
                                         http_client=httpx.Client(transport=httpx.MockTransport(answer)))


@pytest.mark.parametrize("status", [400, 422])
def test_groq_request_rejections_become_inconclusive_not_endless_retries(status):
    report = assemble(bundle("payments_v1"))
    result = _groq(status, {"error": {"message": "json_validate_failed: output was not valid JSON"}}).interpret(
        "Re-check the logs.", report, allowed_targets(report))
    assert result.output.agent_status == "inconclusive" and result.attempts == 1
    assert "json_validate_failed" in result.raw


@pytest.mark.parametrize("status", [401, 404, 500, 429])
def test_groq_outages_and_configuration_errors_still_raise_for_the_outbox(status):
    report = assemble(bundle("payments_v1"))
    with pytest.raises(ProviderUnavailable):
        _groq(status, {"error": {"message": "nope"}}).interpret("Re-check the logs.", report,
                                                               allowed_targets(report))


def test_terminal_delivery_failure_is_reported_on_the_cycle(engine, uow, clock, gate, publish, alice):
    record = publish("payments_v1")
    request_changes(uow, gate, alice, record)

    class Down:
        def interpret(self, *_):
            raise ProviderUnavailable("groq 503")

    relay = Relay(engine, {INTERPRET_EVENT: interpret_handler(engine, Down(), clock=clock)}, max_attempts=2,
                  clock=clock)
    for _ in range(3):
        relay.run_once()
        clock.advance(400)
    with uow() as conn:
        summary = gate.summary(conn, record.report_id)
    [failure] = summary.failed_deliveries
    cycle = summary.cycles[0]
    assert (failure.review_cycle_id, failure.event_type) == (cycle.review_cycle_id, INTERPRET_EVENT)
    assert failure.attempts == 2 and "groq 503" in failure.last_error and failure.event_id.startswith("EVT-")
    assert cycle.state == "interpreting"


# ---- Finding 10: only human review rounds consume the limit -------------------------------------------

def test_upstream_updates_do_not_consume_review_rounds(engine, uow, clock, publish, alice):
    gate = ReviewGate(clock=clock, max_review_cycles=2)
    v1 = publish("payments_v1")
    publish("payments_v2_revised", expected_previous=1)  # an upstream update, not a review round
    decision = request_changes(uow, gate, alice, v1.model_copy(update={"version": 2}))
    assert rows(engine, "SELECT state FROM review_cycles WHERE report_version=2") == [("changes_requested",)]
    assert rows(engine, "SELECT count(*) FROM outbox_events WHERE dedupe_key=:d", d=decision.decision_id) == [(1,)]
    with uow() as conn:
        summary = gate.summary(conn, v1.report_id)
    assert summary.cycles_used == 1 and not summary.limit_reached


def test_f6_revisions_do_consume_review_rounds(engine, uow, clock, publish, alice):
    gate = ReviewGate(clock=clock, max_review_cycles=2)
    v1 = publish("payments_v1")
    cycle_id = to_reinvestigating(engine, clock, uow, gate, alice, v1)
    with unit_of_work(engine) as conn:
        v2 = handle_revision_ready(conn, v1.report_id, bundle("payments_v2_revised"), cycle_id, gate=gate,
                                   reports=ReportService(clock=clock), clock=clock)
    with uow() as conn:
        assert gate.summary(conn, v1.report_id).cycles_used == 2
    request_changes(uow, gate, alice, v2, key="rc-v2")
    assert rows(engine, "SELECT state FROM review_cycles WHERE report_version=2") == [("limit_reached",)]
