"""The dashboard shows every review-cycle state honestly and never loses the reviewer's words."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from observa_hitl.auth import ReviewerDirectory
from observa_hitl.contracts import DecisionCommand
from observa_hitl.gate import ReviewGate
from observa_review_service.app import create_app
from observa_review_service.db import unit_of_work
from observa_review_service.settings import Settings

from review_helpers import write_reviewers


@pytest.fixture()
def directory(tmp_path):
    return ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json"))


@pytest.fixture()
def http(engine, tmp_path, directory):
    settings = Settings(MAX_REVIEW_CYCLES=2, REVIEWERS_FILE=str(tmp_path / "none.json"))
    with TestClient(create_app(settings=settings, engine=engine, reviewers=directory, session_secret="s")) as c:
        c.post("/ui/login", data={"token": "alice-token"})
        yield c


@pytest.fixture()
def reviewed(publish, engine):
    def _publish(name, **kw):
        record = publish(name, **kw)
        with unit_of_work(engine) as conn:
            ReviewGate().open(conn, record.report_id, record.version)
        return record
    return _publish


def set_state(engine, state, version=1):
    with engine.begin() as conn:
        conn.execute(text("UPDATE review_cycles SET state=:s, state_version=state_version+1 "
                          "WHERE report_version=:v"), {"s": state, "v": version})


def fields(html):
    return dict(re.findall(r'name="(csrf_token|idempotency_key)" value="([^"]+)"', html))


def banner(html):
    match = re.search(r'data-testid="cycle-banner" data-state="(\w+)"', html)
    return match.group(1) if match else None


def test_failed_submit_keeps_the_feedback_text_and_the_panel_open(http, reviewed, engine, directory):
    v1 = reviewed("payments_v1")
    page = http.get(f"/ui/reports/{v1.report_id}").text
    words = "Check the payment provider <first> & then orders-service."
    # Another reviewer decides first; our submit now fails with a conflict.
    ReviewGate().submit(lambda: unit_of_work(engine), directory.get("rev-bob"), DecisionCommand(
        report_id=v1.report_id, version=1, decision="approve", idempotency_key="bob"))
    failed = http.post(f"/ui/reports/{v1.report_id}/v/1/decide",
                       data={"decision": "request_changes", "feedback": words, **fields(page)})
    assert failed.status_code == 409
    assert 'data-testid="decision-error"' in failed.text
    assert "Check the payment provider &lt;first&gt; &amp; then orders-service." in failed.text  # escaped, kept
    assert 'data-testid="unsent-feedback"' in failed.text  # shown even though the action bar is gone


def test_blank_feedback_error_reopens_the_panel_with_the_text(http, reviewed):
    v1 = reviewed("payments_v1")
    page = http.get(f"/ui/reports/{v1.report_id}").text
    failed = http.post(f"/ui/reports/{v1.report_id}/v/1/decide",
                       data={"decision": "request_changes", "feedback": "   ", **fields(page)})
    assert failed.status_code == 422
    panel = re.search(r'<div id="request-changes-panel" class="([^"]+)"', failed.text).group(1)
    assert "hidden" not in panel.split()
    assert fields(failed.text)["idempotency_key"] != fields(page)["idempotency_key"]  # a fresh attempt


@pytest.mark.parametrize(("state", "approve", "request_changes"), [
    ("pending_review", True, True),
    ("clarification_needed", True, True),
    ("revision_failed", True, False),
    ("limit_reached", True, False),
    ("interpreting", False, False),
    ("reinvestigating", False, False),
    ("changes_requested", False, False),
    ("approved", False, False),
])
def test_actions_follow_the_cycle_state(http, reviewed, engine, state, approve, request_changes):
    v1 = reviewed("payments_v1")
    set_state(engine, state)
    html = http.get(f"/ui/reports/{v1.report_id}").text
    assert ('data-testid="approve-button"' in html) is approve, state
    assert ('data-testid="request-changes-button"' in html) is request_changes, state
    if state != "pending_review":
        assert banner(html) == state


def test_limit_banner_names_the_limit(http, reviewed, engine):
    v1 = reviewed("payments_v1")
    set_state(engine, "limit_reached")
    html = http.get(f"/ui/reports/{v1.report_id}").text
    assert "1 of 2" in html and "will not be re-investigated" in html.lower()


def test_revision_failure_banner_shows_the_error(http, reviewed, engine):
    v1 = reviewed("payments_v1")
    set_state(engine, "revision_failed")
    with engine.begin() as conn:
        cycle_id = conn.execute(text("SELECT review_cycle_id FROM review_cycles")).scalar_one()
        conn.execute(text("INSERT INTO reinvestigations (request_id, review_cycle_id, feedback_id, status, "
                          "last_error) VALUES ('REQ-1', :c, 'FB-1', 'failed', 'F2 <timed> out')"), {"c": cycle_id})
    html = http.get(f"/ui/reports/{v1.report_id}").text
    assert "F2 &lt;timed&gt; out" in html and "v1 stays current" in html


def test_superseded_version_links_to_the_current_one(http, reviewed, engine):
    v1 = reviewed("payments_v1")
    reviewed("payments_v2_revised", expected_previous=1)
    old = http.get(f"/ui/reports/{v1.report_id}/v/1").text
    assert banner(old) == "superseded" and f'href="/ui/reports/{v1.report_id}/v/2"' in old
    assert 'data-testid="action-bar"' not in old


def test_interpreted_feedback_is_shown_next_to_the_original(http, reviewed, engine):
    v1 = reviewed("payments_v1")
    page = http.get(f"/ui/reports/{v1.report_id}").text
    http.post(f"/ui/reports/{v1.report_id}/v/1/decide",
              data={"decision": "request_changes", "feedback": "Re-check the traces for orders-service.",
                    **fields(page)})
    from observa_hitl.feedback import interpret_handler
    from observa_hitl.interpreter import FakeInterpreter
    from observa_hitl.outbox import INTERPRET_EVENT, Relay
    Relay(engine, {INTERPRET_EVENT: interpret_handler(engine, FakeInterpreter())}).run_once()
    html = http.get(f"/ui/reports/{v1.report_id}").text
    assert 'data-testid="feedback-intent"' in html and "orders-service" in html
    assert banner(html) == "reinvestigating"
