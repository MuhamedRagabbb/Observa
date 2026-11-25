"""POST /api/v1/reports/{id}/versions/{v}/decisions, the review summary and the UI decide route."""
import re

import pytest
from fastapi.testclient import TestClient

from observa_hitl.auth import ReviewerDirectory
from observa_review_service.app import create_app
from observa_review_service.ui import session

from review_helpers import write_reviewers


@pytest.fixture()
def http(engine, tmp_path):
    directory = ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json"))
    with TestClient(create_app(engine=engine, reviewers=directory, session_secret="s")) as client:
        yield client


@pytest.fixture()
def reviewed(publish, engine):
    """publish() plus the review cycle the composition root opens for every new version."""
    from observa_hitl.gate import ReviewGate
    from observa_review_service.db import unit_of_work

    def _publish(name, **kw):
        record = publish(name, **kw)
        with unit_of_work(engine) as conn:
            ReviewGate().open(conn, record.report_id, record.version)
        return record
    return _publish


def auth(token, key=None):
    headers = {"Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def url(record, version=None):
    return f"/api/v1/reports/{record.report_id}/versions/{version or record.version}/decisions"


def test_decision_status_codes_201_200_401_403_409_422(http, reviewed):
    v1 = reviewed("payments_v1")
    body = {"decision": "approve", "feedback": None}
    assert http.post(url(v1), json=body, headers={"Idempotency-Key": "k1"}).status_code == 401
    forbidden = http.post(url(v1), json=body, headers=auth("vic-token", "k1"))
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "NOT_AUTHORIZED"
    missing = http.post(url(v1), json={"decision": "request_changes", "feedback": "  "}, headers=auth("alice-token", "k0"))
    assert missing.status_code == 422 and missing.json()["code"] == "VALIDATION_ERROR"
    no_key = http.post(url(v1), json=body, headers=auth("alice-token"))
    assert no_key.status_code == 422 and no_key.json()["code"] == "VALIDATION_ERROR"
    created = http.post(url(v1), json=body, headers=auth("alice-token", "k1"))
    assert created.status_code == 201
    decision = created.json()
    assert set(decision) == {"decision_id", "idempotency_key", "incident_id", "report_id", "version", "reviewer_id",
                             "decision", "feedback", "decided_at"}
    assert decision["decided_at"].endswith("Z") and decision["version"] == 1
    replay = http.post(url(v1), json=body, headers=auth("alice-token", "k1"))
    assert replay.status_code == 200 and replay.json() == decision
    clash = http.post(url(v1), json={"decision": "request_changes", "feedback": "no"}, headers=auth("alice-token", "k1"))
    assert clash.status_code == 409 and clash.json()["code"] == "IDEMPOTENCY_CONFLICT"
    late = http.post(url(v1), json={"decision": "request_changes", "feedback": "x"}, headers=auth("bob-token", "k2"))
    assert late.status_code == 409 and late.json()["code"] == "VERSION_CONFLICT"


def test_stale_version_409_after_v2(http, reviewed):
    v1 = reviewed("payments_v1")
    reviewed("payments_v2_revised", expected_previous=1)
    stale = http.post(url(v1), json={"decision": "approve"}, headers=auth("alice-token", "s1"))
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"


def test_malformed_body_is_422_envelope(http, reviewed):
    v1 = reviewed("payments_v1")
    bad = http.post(url(v1), json={"decision": "maybe"}, headers=auth("alice-token", "m1"))
    assert bad.status_code == 422 and bad.json()["code"] == "VALIDATION_ERROR"


def test_review_summary_and_projected_status(http, reviewed):
    v1 = reviewed("payments_v1")
    http.post(url(v1), json={"decision": "approve"}, headers=auth("alice-token", "k1"))
    summary = http.get(f"/api/v1/reports/{v1.report_id}/review", headers=auth("vic-token")).json()
    assert summary["current_version"] == 1 and summary["cycles"][0]["state"] == "approved"
    assert summary["decisions"][0]["memory_sync_status"] == "pending"
    report = http.get(f"/api/v1/reports/{v1.report_id}", headers=auth("vic-token")).json()
    assert report["report_version"]["review_status"] == "approved"


def _form(html):
    fields = dict(re.findall(r'name="(csrf_token|idempotency_key)" value="([^"]+)"', html))
    assert set(fields) == {"csrf_token", "idempotency_key"}, html[-2000:]
    return fields


def test_ui_decide_approves_with_session_and_csrf(http, reviewed):
    v1 = reviewed("payments_v1")
    http.post("/ui/login", data={"token": "alice-token"})
    page = http.get(f"/ui/reports/{v1.report_id}")
    assert 'aria-disabled="true"' not in _approve_button(page.text)
    fields = _form(page.text)
    target = f"/ui/reports/{v1.report_id}/v/1/decide"
    assert http.post(target, data={"decision": "approve", "idempotency_key": fields["idempotency_key"]},
                     follow_redirects=False).status_code == 403  # no CSRF token
    done = http.post(target, data={"decision": "approve", **fields}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"].startswith(f"/ui/reports/{v1.report_id}/v/1")
    again = http.post(target, data={"decision": "approve", **fields}, follow_redirects=False)  # double submit
    assert again.status_code == 303
    after = http.get(done.headers["location"]).text
    assert 'data-testid="action-bar"' not in after
    assert "approved" in after.lower() and 'data-testid="decision-history"' in after
    assert 'data-testid="memory-sync"' in after
    summary = http.get(f"/api/v1/reports/{v1.report_id}/review").json()
    assert len(summary["decisions"]) == 1


def test_ui_request_changes_shows_error_for_blank_feedback(http, reviewed):
    v1 = reviewed("payments_v1")
    http.post("/ui/login", data={"token": "alice-token"})
    fields = _form(http.get(f"/ui/reports/{v1.report_id}").text)
    blank = http.post(f"/ui/reports/{v1.report_id}/v/1/decide",
                      data={"decision": "request_changes", "feedback": "   ", **fields}, follow_redirects=False)
    assert blank.status_code == 422 and "feedback" in blank.text.lower()


def test_viewer_sees_no_enabled_actions(http, reviewed):
    v1 = reviewed("payments_v1")
    http.post("/ui/login", data={"token": "vic-token"})
    page = http.get(f"/ui/reports/{v1.report_id}").text
    assert 'aria-disabled="true"' in _approve_button(page)
    assert "read-only access" in page and 'name="csrf_token" value' not in page.split('id="decide-form"')[1][:400]


def _approve_button(html):
    return re.search(r'<button data-testid="approve-button"[^>]*>', html).group(0)


def test_signed_out_users_cannot_reach_ui_decide(http, reviewed):
    v1 = reviewed("payments_v1")
    response = http.post(f"/ui/reports/{v1.report_id}/v/1/decide", data={"decision": "approve"},
                         follow_redirects=False)
    assert response.status_code in (303, 403)
    assert session.COOKIE not in response.cookies
