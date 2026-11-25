"""The outbox worker CLI, the file-backed F5 mock and the internal retry endpoint."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from observa_hitl.auth import ReviewerDirectory
from observa_hitl.contracts import DecisionCommand
from observa_hitl.gate import ReviewGate
from observa_review_service.app import create_app
from observa_review_service.db import unit_of_work
from observa_review_service.mocks.memory_sink import FileMemorySink
from observa_review_service.settings import Settings
from observa_review_service.worker import main

from review_helpers import SERVICE_TOKEN, sha, write_reviewers


@pytest.fixture()
def approved(engine, publish):
    record = publish("payments_v1")
    reviewer = ReviewerDirectory.from_file(write_reviewers(__import__("pathlib").Path(engine.url.database).parent /
                                                           "reviewers.json")).get("rev-alice")
    gate = ReviewGate()
    with unit_of_work(engine) as conn:
        gate.open(conn, record.report_id, 1)
    return gate.submit(lambda: unit_of_work(engine), reviewer, DecisionCommand(
        report_id=record.report_id, version=1, decision="approve", idempotency_key="w-1")).decision


def test_worker_once_delivers_to_the_file_sink(db_url, tmp_path, approved, capsys):
    memory = tmp_path / "memory"
    main(["--once", "--database-url", db_url, "--memory-dir", str(memory)])
    assert "delivered 1" in capsys.readouterr().out
    stored = json.loads((memory / f"{approved.decision_id}.json").read_text(encoding="utf-8"))
    assert stored["decision_id"] == approved.decision_id and stored["version"] == 1
    main(["--once", "--database-url", db_url, "--memory-dir", str(memory)])  # nothing left; still one record
    assert len(list(memory.glob("*.json"))) == 1


def test_file_sink_is_idempotent_and_refuses_a_different_record(tmp_path):
    from observa_hitl.ports import ApprovedReportHandoff
    sink = FileMemorySink(tmp_path)
    handoff = ApprovedReportHandoff(decision_id="DEC-1", incident_id="INC-1", report_id="RPT-1", version=1,
                                    content_ref="sha256:x", content_digest="x", approved_at="2026-11-25T12:00:00Z",
                                    reviewer_id="r", evidence_ids=["e"])
    sink.store(handoff)
    sink.store(handoff)
    assert len(list(tmp_path.glob("*.json"))) == 1
    with pytest.raises(ValueError):
        sink.store(handoff.model_copy(update={"version": 2}))


def test_internal_retry_endpoint_needs_the_service_token_and_requeues(engine, tmp_path, approved):
    with engine.begin() as conn:
        conn.execute(text("UPDATE outbox_events SET status='failed', attempts=8, last_error='down'"))
        event_id = conn.execute(text("SELECT event_id FROM outbox_events")).scalar_one()
    settings = Settings(SERVICE_TOKEN_SHA256=sha(SERVICE_TOKEN), REVIEWERS_FILE=str(tmp_path / "none.json"))
    with TestClient(create_app(settings=settings, engine=engine)) as client:
        url = f"/internal/v1/outbox/{event_id}/retry"
        assert client.post(url).status_code == 401
        assert client.post(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = client.post(url, headers={"Authorization": f"Bearer {SERVICE_TOKEN}"})
        assert ok.status_code == 200 and ok.json() == {"event_id": event_id, "status": "pending"}
        again = client.post(url, headers={"Authorization": f"Bearer {SERVICE_TOKEN}"})
        assert again.status_code == 409 and again.json()["code"] == "VERSION_CONFLICT"
    with engine.connect() as conn:
        assert tuple(conn.execute(text("SELECT status, attempts FROM outbox_events")).one()) == ("pending", 0)


def test_internal_endpoints_are_closed_without_a_configured_service_token(engine, tmp_path, approved):
    settings = Settings(SERVICE_TOKEN_SHA256=None, REVIEWERS_FILE=str(tmp_path / "none.json"))
    with TestClient(create_app(settings=settings, engine=engine)) as client:
        assert client.post("/internal/v1/outbox/EVT-x/retry",
                           headers={"Authorization": f"Bearer {SERVICE_TOKEN}"}).status_code == 401


def test_worker_interprets_change_requests_with_the_configured_fake(db_url, engine, publish, tmp_path, capsys):
    record = publish("payments_v1")
    reviewer = ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json")).get("rev-alice")
    gate = ReviewGate()
    with unit_of_work(engine) as conn:
        gate.open(conn, record.report_id, 1)
    gate.submit(lambda: unit_of_work(engine), reviewer, DecisionCommand(
        report_id=record.report_id, version=1, decision="request_changes", idempotency_key="w-rc",
        feedback="Re-check the traces for orders-service."))
    main(["--once", "--database-url", db_url, "--memory-dir", str(tmp_path / "memory")])
    with engine.connect() as conn:
        assert conn.execute(text("SELECT interpreter, agent_status FROM feedback_intents")).one() == \
            ("fake", "completed")
        # One run delivers the follow-on re-investigation too; the mock F2 answered with v2.
        assert tuple(conn.execute(text("SELECT status, attempts FROM outbox_events "
                                       "WHERE event_type='reinvestigation.request.v1'")).one()) == ("delivered", 1)
        assert [tuple(r) for r in conn.execute(text(
            "SELECT report_version, state FROM review_cycles ORDER BY report_version"))] == \
            [(1, "superseded"), (2, "pending_review")]


def test_groq_interpreter_needs_an_api_key(engine):
    from observa_review_service.worker import build_interpreter
    with pytest.raises(ValueError, match="GROQ_API_KEY"):
        build_interpreter(Settings(INTERPRETER="groq", GROQ_API_KEY=None))
    assert build_interpreter(Settings(INTERPRETER="fake")).__class__.__name__ == "FakeInterpreter"
    groq = build_interpreter(Settings(INTERPRETER="groq", GROQ_API_KEY="gsk_test"))
    assert groq.__class__.__name__ == "GroqInterpreter"  # built without any network call


def _request_changes(engine, tmp_path, record, feedback, key):
    reviewer = ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json")).get("rev-alice")
    gate = ReviewGate()
    with unit_of_work(engine) as conn:
        gate.open(conn, record.report_id, record.version)
    gate.submit(lambda: unit_of_work(engine), reviewer, DecisionCommand(
        report_id=record.report_id, version=record.version, decision="request_changes", idempotency_key=key,
        feedback=feedback))


def test_worker_closes_the_loop_with_the_mock_f2(db_url, engine, publish, tmp_path, capsys):
    """Mock F2 answers from the fixture bundles: a revised bundle for the incident -> vN+1 for review;
    none -> the revision fails visibly and vN stays current."""
    payments = publish("payments_v1")
    other = publish("db_exhaustion")
    _request_changes(engine, tmp_path, payments, "Re-check the traces for orders-service.", "loop-1")
    _request_changes(engine, tmp_path, other, "Re-check the logs for orders-service.", "loop-2")
    main(["--once", "--database-url", db_url, "--memory-dir", str(tmp_path / "memory")])
    with engine.connect() as conn:
        cycles = {tuple(r[:2]): r[2] for r in conn.execute(text(
            "SELECT report_id, report_version, state FROM review_cycles"))}
        heads = dict(conn.execute(text("SELECT report_id, current_version FROM report_heads")).all())
        failures = conn.execute(text("SELECT status, last_error FROM reinvestigations ORDER BY status")).all()
    assert cycles[(payments.report_id, 1)] == "superseded" and cycles[(payments.report_id, 2)] == "pending_review"
    assert heads[payments.report_id] == 2 and heads[other.report_id] == 1
    assert cycles[(other.report_id, 1)] == "revision_failed"
    assert [s for s, _ in failures] == ["completed", "failed"] and "no revised" in failures[1][1]
    assert "pending 0" in capsys.readouterr().out
