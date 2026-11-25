"""Reviewer authentication over HTTP: Bearer tokens, the signed UI session and CSRF on UI POSTs."""
import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from observa_hitl.auth import Reviewer, ReviewerDirectory
from observa_review_service.app import create_app
from observa_review_service.ui import session

from review_helpers import write_reviewers


@pytest.fixture()
def directory(tmp_path):
    return ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json"))


@pytest.fixture()
def app(engine, directory):
    app = create_app(engine=engine, reviewers=directory, session_secret="test-secret")

    @app.post("/__probe__/reviewer-only")
    def reviewer_only(reviewer: Reviewer = Depends(session.require_reviewer)):
        return {"reviewer_id": reviewer.reviewer_id}

    return app


@pytest.fixture()
def http(app):
    with TestClient(app) as client:
        yield client


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_viewer_cannot_decide_403(http):
    response = http.post("/__probe__/reviewer-only", headers=bearer("vic-token"))
    assert response.status_code == 403
    assert response.json()["code"] == "NOT_AUTHORIZED"
    assert http.post("/__probe__/reviewer-only", headers=bearer("alice-token")).json() == {"reviewer_id": "rev-alice"}


def test_missing_or_wrong_token_is_401_envelope(http):
    for headers in ({}, bearer("wrong"), {"Authorization": "Basic YWxpY2U="}):
        response = http.post("/__probe__/reviewer-only", headers=headers)
        assert response.status_code == 401
        assert response.json()["code"] == "NOT_AUTHORIZED"
        assert response.headers["www-authenticate"] == "Bearer"


def test_read_api_requires_a_viewer_when_reviewers_are_configured(http, publish):
    record = publish("payments_v1")
    assert http.get(f"/api/v1/reports/{record.report_id}").status_code == 401
    assert http.get(f"/api/v1/reports/{record.report_id}", headers=bearer("vic-token")).status_code == 200


def test_ui_login_sets_signed_session_and_logout_needs_csrf(http):
    assert http.get("/ui", follow_redirects=False).status_code == 303  # not signed in -> login page
    page = http.get("/ui/login")
    assert page.status_code == 200 and 'name="token"' in page.text
    wrong = http.post("/ui/login", data={"token": "wrong"}, follow_redirects=False)
    assert wrong.status_code == 401 and session.COOKIE not in wrong.cookies
    ok = http.post("/ui/login", data={"token": "alice-token"}, follow_redirects=False)
    assert ok.status_code == 303
    cookie = ok.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie.replace("samesite", "SameSite").replace("Lax", "lax")
    assert http.get("/ui").status_code == 200
    # A session cookie works for reviewer-only routes too.
    assert http.post("/__probe__/reviewer-only").json() == {"reviewer_id": "rev-alice"}


def test_login_never_redirects_off_the_dashboard(http):
    for target in ("https://evil.example/", "//evil.example/ui", "/api/v1/reports"):
        response = http.post("/ui/login", data={"token": "alice-token", "next": target}, follow_redirects=False)
        assert response.headers["location"] == "/ui", target
    ok = http.post("/ui/login", data={"token": "alice-token", "next": "/ui/reports/RPT-1"}, follow_redirects=False)
    assert ok.headers["location"] == "/ui/reports/RPT-1"


def test_ui_post_without_csrf_rejected(http):
    http.post("/ui/login", data={"token": "alice-token"})
    assert http.post("/ui/logout", data={}, follow_redirects=False).status_code == 403
    assert http.post("/ui/logout", data={"csrf_token": "forged"}, follow_redirects=False).status_code == 403
    token = session.csrf_token_from_cookie(http.app, http.cookies.get(session.COOKIE))
    assert http.post("/ui/logout", data={"csrf_token": token}, follow_redirects=False).status_code == 303
    assert http.get("/ui", follow_redirects=False).status_code == 303  # signed out


def test_tampered_session_cookie_is_ignored(http):
    http.post("/ui/login", data={"token": "alice-token"})
    value = http.cookies.get(session.COOKIE)
    http.cookies.clear()
    http.cookies.set(session.COOKIE, value[:-2] + ("AA" if not value.endswith("AA") else "BB"))
    assert http.get("/ui", follow_redirects=False).status_code == 303


def test_without_configured_reviewers_reads_stay_open_and_decisions_need_auth(engine, publish):
    record = publish("payments_v1")
    with TestClient(create_app(engine=engine)) as client:
        assert client.get(f"/api/v1/reports/{record.report_id}").status_code == 200
