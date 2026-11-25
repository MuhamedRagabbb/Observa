"""Regressions for the whole-branch review findings (HTTP and dashboard side)."""
import re
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from observa_hitl.auth import ReviewerDirectory
from observa_hitl.gate import ReviewGate
from observa_review_service.app import create_app
from observa_review_service.db import unit_of_work
from observa_review_service.settings import Settings

from review_helpers import write_reviewers


@pytest.fixture()
def http(engine, tmp_path):
    directory = ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json"))
    settings = Settings(REVIEWERS_FILE=str(tmp_path / "none.json"))
    with TestClient(create_app(settings=settings, engine=engine, reviewers=directory, session_secret="s")) as c:
        yield c


@pytest.fixture()
def reviewed(publish, engine):
    def _publish(name, **kw):
        record = publish(name, **kw)
        with unit_of_work(engine) as conn:
            ReviewGate().open(conn, record.report_id, record.version)
        return record
    return _publish


class _Form(HTMLParser):
    """Collects the decide form's controls the way a browser's constraint validation sees them."""

    def __init__(self):
        super().__init__()
        self.in_form, self.required, self.approve = False, [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "decide-form":
            self.in_form = True
        elif self.in_form and "required" in a and tag in ("textarea", "input", "select"):
            self.required.append(a.get("name"))
        elif self.in_form and a.get("data-testid") == "approve-button":
            self.approve = a

    def handle_endtag(self, tag):
        if tag == "form":
            self.in_form = False


# ---- Finding 1 (Critical): Approve must submit even though the change-request panel is hidden ------

@pytest.mark.parametrize("state", ["pending_review", "clarification_needed"])
def test_approve_is_not_blocked_by_the_hidden_feedback_field(http, reviewed, engine, state):
    v1 = reviewed("payments_v1")
    with engine.begin() as conn:
        conn.execute(text("UPDATE review_cycles SET state=:s"), {"s": state})
    http.post("/ui/login", data={"token": "alice-token"})
    form = _Form()
    form.feed(http.get(f"/ui/reports/{v1.report_id}").text)
    assert form.approve is not None
    # A browser refuses to submit a form with an empty `required` control, even a hidden one, unless
    # the submitting button opts out of validation.
    assert not form.required or "formnovalidate" in form.approve, form.required


# ---- Finding 5: the JSON decision API is Bearer-only (a session cookie carries no CSRF proof) --------

def test_decision_api_ignores_the_dashboard_session_cookie(http, reviewed):
    v1 = reviewed("payments_v1")
    http.post("/ui/login", data={"token": "alice-token"})  # the client now holds a valid session cookie
    response = http.post(f"/api/v1/reports/{v1.report_id}/versions/1/decisions", json={"decision": "approve"},
                         headers={"Idempotency-Key": "cookie-only"})
    assert response.status_code == 401
    ok = http.post(f"/api/v1/reports/{v1.report_id}/versions/1/decisions", json={"decision": "approve"},
                   headers={"Idempotency-Key": "bearer", "Authorization": "Bearer alice-token"})
    assert ok.status_code == 201


# ---- Finding 4: a delivery that gave up is shown, with the operator's way out -------------------------

def test_terminal_delivery_failure_banner(http, reviewed, engine):
    v1 = reviewed("payments_v1")
    http.post("/ui/login", data={"token": "alice-token"})
    page = http.get(f"/ui/reports/{v1.report_id}").text
    fields = dict(re.findall(r'name="(csrf_token|idempotency_key)" value="([^"]+)"', page))
    http.post(f"/ui/reports/{v1.report_id}/v/1/decide",
              data={"decision": "request_changes", "feedback": "Re-check the logs.", **fields})
    with engine.begin() as conn:
        conn.execute(text("UPDATE review_cycles SET state='interpreting'"))
        conn.execute(text("UPDATE outbox_events SET status='failed', attempts=8, last_error='groq <down>'"))
        event_id = conn.execute(text("SELECT event_id FROM outbox_events")).scalar_one()
    html = http.get(f"/ui/reports/{v1.report_id}").text
    assert 'data-state="delivery_failed"' in html
    assert "groq &lt;down&gt;" in html and event_id in html and "/internal/v1/outbox/" in html
