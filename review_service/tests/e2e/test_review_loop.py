"""End to end over HTTP: the real app (TestClient/httpx), the real outbox relay and worker wiring,
the fake interpreter, the fixture-backed F2 mock and an F5 mock. Only F2/F5/Groq are stand-ins.

    POST /internal/v1/reports (v1) -> reviewer requests changes -> worker interprets -> mock F2
    re-investigates -> v2 published for review -> reviewer approves v2 -> exactly one F5 record.
"""
import json
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from observa_hitl.auth import ReviewerDirectory
from observa_review_service.app import create_app
from observa_review_service.mocks.memory_sink import FakeMemorySink
from observa_review_service.settings import Settings
from observa_review_service.worker import build_relay, drain

from review_helpers import FIXTURES, SERVICE_TOKEN, sha, write_reviewers

SERVICE = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
ALICE = {"Authorization": "Bearer alice-token"}
BOB = {"Authorization": "Bearer bob-token"}


def bundle(name):
    return json.loads((FIXTURES / "bundles" / f"{name}.json").read_text(encoding="utf-8"))


class Clock:
    """Starts at the real time (the app stamps events with it) and can jump ahead for retries."""

    def __init__(self):
        self.offset = timedelta()

    def __call__(self):
        return datetime.now(timezone.utc) + self.offset

    def advance(self, seconds):
        self.offset += timedelta(seconds=seconds)


class Loop:
    def __init__(self, client, relay, sink, clock):
        self.http, self.relay, self.sink, self.clock = client, relay, sink, clock

    def publish(self, name):
        response = self.http.post("/internal/v1/reports", json=bundle(name), headers=SERVICE)
        assert response.status_code in (200, 201), response.text
        return response.json()

    def decide(self, report_id, version, decision, key, feedback=None, who=ALICE):
        return self.http.post(f"/api/v1/reports/{report_id}/versions/{version}/decisions",
                              json={"decision": decision, "feedback": feedback},
                              headers={**who, "Idempotency-Key": key})

    def work(self):
        return drain(self.relay)

    def review(self, report_id):
        return self.http.get(f"/api/v1/reports/{report_id}/review", headers=ALICE).json()

    def versions(self, report_id):
        return [(v["version"], v["review_status"]) for v in
                self.http.get(f"/api/v1/reports/{report_id}/versions", headers=ALICE).json()]


@pytest.fixture()
def loop(engine, tmp_path):
    settings = Settings(SERVICE_TOKEN_SHA256=sha(SERVICE_TOKEN), REVIEWERS_FILE=str(tmp_path / "none.json"),
                        MEMORY_SINK_DIR=str(tmp_path / "memory"), INTERPRETER="fake")
    directory = ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json"))
    sink, clock = FakeMemorySink(), Clock()
    with TestClient(create_app(settings=settings, engine=engine, reviewers=directory, session_secret="e2e")) as c:
        yield Loop(c, build_relay(settings, engine, memory_sink=sink, clock=clock), sink, clock)


def test_e2e_reject_revise_approve_exact_version(loop):
    v1 = loop.publish("payments_v1")
    report_id = v1["report_id"]
    assert v1["version"] == 1 and loop.versions(report_id) == [(1, "pending_review")]
    assert loop.decide(report_id, 1, "request_changes", "rc-1",
                       "Re-check the traces for orders-service before blaming payments.").status_code == 201
    assert loop.work() >= 2  # interpret, then the targeted re-investigation (mock F2 answers with v2)
    assert loop.versions(report_id) == [(1, "changes_requested"), (2, "pending_review")]
    current = loop.http.get(f"/api/v1/reports/{report_id}", headers=ALICE).json()["report_version"]
    assert current["version"] == 2 and current["previous_version"] == 1
    review = loop.review(report_id)
    assert current["review_cycle_id"] == review["cycles"][0]["review_cycle_id"]  # v2 names the cycle it answers
    assert [(c["report_version"], c["state"]) for c in review["cycles"]] == [(1, "superseded"), (2, "pending_review")]
    assert review["feedback_intents"][0]["agent_status"] == "completed"
    assert review["reinvestigations"][0]["status"] == "completed"
    assert loop.sink.calls == 0  # nothing reached memory before an explicit approval
    stale = loop.decide(report_id, 1, "approve", "approve-v1")
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    assert loop.decide(report_id, 2, "approve", "approve-v2").status_code == 201
    loop.work()
    [record] = loop.sink.records.values()
    assert (record.report_id, record.version, record.reviewer_id) == (report_id, 2, "rev-alice")
    assert loop.versions(report_id) == [(1, "changes_requested"), (2, "approved")]
    assert [d["memory_sync_status"] for d in loop.review(report_id)["decisions"]] == ["not_requested", "stored"]


def test_e2e_repeated_approval_callback_single_record(loop):
    report_id = loop.publish("payments_v1")["report_id"]
    first = loop.decide(report_id, 1, "approve", "same-key")
    again = loop.decide(report_id, 1, "approve", "same-key")  # double click / retried POST
    assert (first.status_code, again.status_code) == (201, 200) and first.json() == again.json()
    rival = loop.decide(report_id, 1, "approve", "bob-key", who=BOB)
    assert rival.status_code == 409 and rival.json()["code"] == "VERSION_CONFLICT"
    loop.sink.crash_after_store = True  # F5 stores, then the worker dies before acknowledging
    loop.work()
    loop.clock.advance(400)  # time passes: the retry is due
    loop.work()
    assert loop.sink.calls == 2 and len(loop.sink.records) == 1
    assert loop.review(report_id)["decisions"][0]["memory_sync_status"] == "stored"


def test_e2e_inconclusive_report_reviewable_without_forced_remediation(loop):
    published = loop.publish("inconclusive")
    report_id = published["report_id"]
    assert "root_cause_inconclusive" in published["uncertainty_flags"]
    loop.http.post("/ui/login", data={"token": "alice-token"})
    page = loop.http.get(f"/ui/reports/{report_id}").text
    assert 'data-testid="approve-button"' in page and 'aria-disabled="true"' not in \
        re.search(r'<button data-testid="approve-button"[^>]*>', page).group(0)
    # Unclear feedback asks for clarification and dispatches nothing.
    assert loop.decide(report_id, 1, "request_changes", "unclear", "This does not look right to me.").status_code == 201
    loop.work()
    review = loop.review(report_id)
    assert review["cycles"][0]["state"] == "clarification_needed" and review["reinvestigations"] == []
    # The reviewer may accept the inconclusive assessment as it stands: no remediation is forced.
    assert loop.decide(report_id, 1, "approve", "accept-as-is").status_code == 201
    loop.work()
    assert len(loop.sink.records) == 1


@pytest.mark.parametrize("name", ["db_exhaustion", "bad_deployment"])
def test_e2e_db_exhaustion_and_bad_deployment_render(loop, name):
    published = loop.publish(name)
    report_id = published["report_id"]
    loop.http.post("/ui/login", data={"token": "vic-token"})
    page = loop.http.get(f"/ui/reports/{report_id}")
    title = bundle(name)["incident"]["title"]
    assert page.status_code == 200 and title.replace("'", "&#39;") in page.text
    assert 'data-testid="action-bar"' in page.text  # visible to a viewer, but read-only
    markdown = loop.http.get(f"/api/v1/reports/{report_id}/versions/1/report.md", headers=ALICE)
    assert markdown.status_code == 200 and "Version 1" in markdown.text and "pending human review" in markdown.text
    assert markdown.headers["content-type"] == "text/markdown; charset=utf-8" and "�" not in markdown.text
    assert loop.http.get("/ui").status_code == 200


def test_e2e_publish_is_idempotent_and_needs_the_service_token(loop):
    assert loop.http.post("/internal/v1/reports", json=bundle("payments_v1"), headers=ALICE).status_code == 401
    first = loop.publish("payments_v1")
    again = loop.http.post("/internal/v1/reports", json=bundle("payments_v1"), headers=SERVICE)
    assert again.status_code == 200 and again.json()["version"] == first["version"] == 1
