"""Internal service endpoints: F7 publish intake, F2 revision results and failures (service token)."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from observa_hitl.auth import ReviewerDirectory
from observa_hitl.contracts import DecisionCommand
from observa_hitl.gate import ReviewGate
from observa_review_service.app import create_app
from observa_review_service.db import unit_of_work
from observa_review_service.settings import Settings

from review_helpers import FIXTURES, SERVICE_TOKEN, sha, write_reviewers

SERVICE = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


def body(name):
    return json.loads((FIXTURES / "bundles" / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def http(engine, tmp_path):
    settings = Settings(SERVICE_TOKEN_SHA256=sha(SERVICE_TOKEN), REVIEWERS_FILE=str(tmp_path / "none.json"))
    directory = ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json"))
    with TestClient(create_app(settings=settings, engine=engine, reviewers=directory)) as client:
        yield client


def states(engine):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            "SELECT report_version, state FROM review_cycles ORDER BY report_version"))]


def test_publish_intake_opens_a_review_cycle_and_is_idempotent(http, engine):
    assert http.post("/internal/v1/reports", json=body("payments_v1")).status_code == 401
    first = http.post("/internal/v1/reports", json=body("payments_v1"), headers=SERVICE)
    assert first.status_code == 201 and first.json()["version"] == 1
    again = http.post("/internal/v1/reports", json=body("payments_v1"), headers=SERVICE)
    assert again.status_code == 200 and again.json()["version"] == 1
    assert states(engine) == [(1, "pending_review")]
    bad = http.post("/internal/v1/reports", json={"incident": {}}, headers=SERVICE)
    assert bad.status_code == 422 and bad.json()["code"] == "VALIDATION_ERROR"


def test_upstream_update_publishes_next_version_and_supersedes_the_open_cycle(http, engine):
    http.post("/internal/v1/reports", json=body("payments_v1"), headers=SERVICE)
    v2 = http.post("/internal/v1/reports", json=body("payments_v2_revised"), headers=SERVICE)
    assert v2.status_code == 201 and v2.json()["version"] == 2
    assert states(engine) == [(1, "superseded"), (2, "pending_review")]


def _reinvestigating(engine, report_id):
    reviewer = ReviewerDirectory.from_file(write_reviewers(
        __import__("pathlib").Path(engine.url.database).parent / "r.json")).get("rev-alice")
    gate = ReviewGate()
    gate.submit(lambda: unit_of_work(engine), reviewer, DecisionCommand(
        report_id=report_id, version=1, decision="request_changes", feedback="Re-check the traces.",
        idempotency_key="int-1"))
    with engine.begin() as conn:  # what the interpreter does for clear feedback (Task 5.2)
        conn.execute(text("UPDATE review_cycles SET state='reinvestigating'"))
        cycle_id = conn.execute(text("SELECT review_cycle_id FROM review_cycles")).scalar_one()
        conn.execute(text("INSERT INTO reinvestigations (request_id, review_cycle_id, feedback_id, status) "
                          "VALUES ('REQ-1', :c, 'FB-1', 'requested')"), {"c": cycle_id})
    return cycle_id


def test_revision_result_creates_v2_for_review(http, engine):
    report_id = http.post("/internal/v1/reports", json=body("payments_v1"), headers=SERVICE).json()["report_id"]
    cycle_id = _reinvestigating(engine, report_id)
    url = f"/internal/v1/reports/{report_id}/revisions"
    payload = {"review_cycle_id": cycle_id, "inputs": body("payments_v2_revised")}
    assert http.post(url, json=payload).status_code == 401
    created = http.post(url, json=payload, headers=SERVICE)
    assert created.status_code == 201 and created.json()["version"] == 2
    assert created.json()["review_cycle_id"] == cycle_id
    assert states(engine) == [(1, "superseded"), (2, "pending_review")]
    late = http.post(url, json={"review_cycle_id": "CYCLE-nope", "inputs": body("payments_v2_revised")},
                     headers=SERVICE)
    assert late.status_code == 404


def test_revision_for_a_cycle_that_is_not_reinvestigating_is_409(http, engine):
    report_id = http.post("/internal/v1/reports", json=body("payments_v1"), headers=SERVICE).json()["report_id"]
    with engine.connect() as conn:
        cycle_id = conn.execute(text("SELECT review_cycle_id FROM review_cycles")).scalar_one()
    response = http.post(f"/internal/v1/reports/{report_id}/revisions", headers=SERVICE,
                         json={"review_cycle_id": cycle_id, "inputs": body("payments_v2_revised")})
    assert response.status_code == 409 and response.json()["code"] == "VERSION_CONFLICT"


def test_reinvestigation_failure_keeps_v1_current(http, engine):
    report_id = http.post("/internal/v1/reports", json=body("payments_v1"), headers=SERVICE).json()["report_id"]
    _reinvestigating(engine, report_id)
    url = "/internal/v1/reinvestigations/REQ-1/failed"
    assert http.post(url, json={"error": "x"}).status_code == 401
    failed = http.post(url, json={"error": "F2 timed out"}, headers=SERVICE)
    assert failed.status_code == 200 and failed.json() == {"request_id": "REQ-1", "status": "failed"}
    assert states(engine) == [(1, "revision_failed")]
    assert http.post("/internal/v1/reinvestigations/REQ-nope/failed", json={"error": "x"},
                     headers=SERVICE).status_code == 404
    current = http.get(f"/api/v1/reports/{report_id}", headers={"Authorization": "Bearer vic-token"}).json()
    assert current["report_version"]["version"] == 1
