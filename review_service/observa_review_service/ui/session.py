"""Who is asking: Bearer tokens for the API, a signed session cookie for the dashboard, CSRF for UI POSTs.

Identity comes from F6's ReviewerDirectory (app.state.reviewers). Without a configured directory
(F6 not installed, or no reviewers file) reads stay open on localhost and nobody can decide.
Authorization of a decision itself happens in the F6 ReviewGate, which also audits rejections.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import TYPE_CHECKING

from fastapi import Request

from observa_investigation.contracts import ErrorEnvelope

if TYPE_CHECKING:  # F6 is optional; only its types are needed here
    from observa_hitl.auth import Reviewer, ReviewerDirectory

COOKIE = "observa_session"
MAX_AGE_SECONDS = 8 * 3600


class AuthFailure(Exception):
    """401 (who are you?) or 403 (not allowed); rendered as an ErrorEnvelope."""

    def __init__(self, status: int, message: str, operation_id: str = "auth"):
        self.status = status
        self.envelope = ErrorEnvelope(code="NOT_AUTHORIZED", message=message, retryable=False,
                                      operation_id=operation_id)
        super().__init__(message)


class LoginRequired(Exception):
    """A dashboard page was requested without a session: redirect to /ui/login."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SessionSigner:
    def __init__(self, secret: str | bytes | None):
        # Without SESSION_SECRET every process gets a fresh random key: sessions end on restart.
        self._key = (secret.encode() if isinstance(secret, str) else secret) or secrets.token_bytes(32)

    def _sign(self, body: str) -> str:
        return _b64(hmac.new(self._key, body.encode(), hashlib.sha256).digest())

    def dumps(self, data: dict) -> str:
        body = _b64(json.dumps(data, sort_keys=True, separators=(",", ":")).encode())
        return f"{body}.{self._sign(body)}"

    def loads(self, value: str | None, *, now: float | None = None) -> dict | None:
        if not value or value.count(".") != 1:
            return None
        body, signature = value.split(".")
        if not hmac.compare_digest(self._sign(body), signature):
            return None
        try:
            data = json.loads(_unb64(body))
        except ValueError:
            return None
        issued = data.get("iat")
        if not isinstance(issued, (int, float)) or (now or time.time()) - issued > MAX_AGE_SECONDS:
            return None
        return data


def new_session(reviewer: Reviewer) -> dict:
    return {"rid": reviewer.reviewer_id, "csrf": secrets.token_urlsafe(32), "iat": int(time.time())}


def _directory(request: Request) -> ReviewerDirectory | None:
    return request.app.state.reviewers


def session_data(request: Request) -> dict | None:
    return request.app.state.session_signer.loads(request.cookies.get(COOKIE))


def identity(request: Request) -> Reviewer | None:
    directory = _directory(request)
    if directory is None:
        return None
    header = request.headers.get("authorization")
    if header is not None:  # an explicit header is never second-guessed by a cookie
        scheme, _, token = header.partition(" ")
        return directory.authenticate(token.strip()) if scheme.lower() == "bearer" else None
    data = session_data(request)
    return directory.get(data["rid"]) if data and isinstance(data.get("rid"), str) else None


def require_viewer(request: Request) -> Reviewer:
    reviewer = identity(request)
    if reviewer is None or not reviewer.can_view:
        raise AuthFailure(401, "A valid reviewer token or session is required.")
    return reviewer


def require_reviewer(request: Request) -> Reviewer:
    reviewer = require_viewer(request)
    if not reviewer.can_review:
        raise AuthFailure(403, f"{reviewer.reviewer_id} may read reports but may not review them.")
    return reviewer


def read_access(request: Request) -> Reviewer | None:
    """API reads: open when no reviewers are configured, otherwise a viewer is required."""
    return None if _directory(request) is None else require_viewer(request)


def ui_viewer(request: Request) -> Reviewer | None:
    """Dashboard pages: open when no reviewers are configured, otherwise sign in first."""
    if _directory(request) is None:
        return None
    reviewer = identity(request)
    if reviewer is None:
        raise LoginRequired()
    return reviewer


def verify_csrf(request: Request, presented: str | None) -> dict:
    data = session_data(request)
    expected = (data or {}).get("csrf")
    if not expected or not presented or not hmac.compare_digest(str(expected), str(presented)):
        raise AuthFailure(403, "The form is missing a valid CSRF token; reload the page and try again.")
    return data


def csrf_token_from_cookie(app, cookie: str | None) -> str | None:
    data = app.state.session_signer.loads(cookie)
    return data.get("csrf") if data else None
