"""Reviewer dashboard routes: report pages, reviewer sign-in and (with F6) the review actions.

Every template value is autoescaped. Narrative text goes through markdown-it with raw
HTML disabled, so upstream (LLM-written) claims can format text but never inject markup.
"""
import uuid
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlalchemy import Connection

from observa_reports.errors import ReportError
from observa_reports.service import ReportService

from ..views import report_version
from . import session

router = APIRouter(include_in_schema=False)
reports = ReportService()
_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).disable("image")


def _md(text: str | None) -> Markup:
    return Markup(_markdown.render(text or ""))


def _ts(value) -> str:
    return value.isoformat().replace("+00:00", "Z") if value else "not recorded"


def _ts_short(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC") if value else "not recorded"


_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True,
                   trim_blocks=True, lstrip_blocks=True)
_env.filters.update(md=_md, ts=_ts, ts_short=_ts_short)
templates = Jinja2Templates(env=_env)


def connection(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.connect() as conn:
        yield conn


@router.get("/")
def root() -> RedirectResponse:
    return RedirectResponse("/ui")


def _page_context(request: Request, viewer) -> dict:
    data = session.session_data(request) if viewer is not None else None
    return {"viewer": viewer, "csrf_token": (data or {}).get("csrf"),
            "sign_in_configured": request.app.state.reviewers is not None}


def _safe_next(target: str | None) -> str:
    # Only same-site dashboard paths: never an absolute or scheme-relative URL.
    return target if target and target.startswith("/ui") and not target.startswith("//") else "/ui"


@router.get("/ui/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None):
    return templates.TemplateResponse(request, "login.html.j2", {
        "configured": request.app.state.reviewers is not None, "next": _safe_next(next), "error": None,
        **_page_context(request, None)})


@router.post("/ui/login", response_class=HTMLResponse)
def login(request: Request, token: str = Form(""), next: str = Form("/ui")):
    directory = request.app.state.reviewers
    reviewer = directory.authenticate(token) if directory is not None else None
    if reviewer is None:
        return templates.TemplateResponse(request, "login.html.j2", {
            "configured": directory is not None, "next": _safe_next(next),
            "error": "That token is not recognised.", **_page_context(request, None)}, status_code=401)
    response = RedirectResponse(_safe_next(next), status_code=303)
    response.set_cookie(session.COOKIE, request.app.state.session_signer.dumps(session.new_session(reviewer)),
                        max_age=session.MAX_AGE_SECONDS, httponly=True, samesite="lax", path="/")
    return response


@router.post("/ui/logout")
def logout(request: Request, csrf_token: str | None = Form(None)):
    session.verify_csrf(request, csrf_token)
    response = RedirectResponse("/ui/login", status_code=303)
    response.delete_cookie(session.COOKIE, path="/")
    return response


@router.get("/ui", response_class=HTMLResponse)
def index(request: Request, conn: Connection = Depends(connection), viewer=Depends(session.ui_viewer)):
    cards = []
    for record in reports.catalog(conn):
        stored = reports.get(conn, record.report_id, record.version)
        cards.append({"version": report_version(conn, record, request.app.state.status_reader),
                      "content": stored.content})
    return templates.TemplateResponse(request, "index.html.j2", {"cards": cards, **_page_context(request, viewer)})


@router.get("/ui/reports/{report_id}", response_class=HTMLResponse)
def current_report(report_id: str, request: Request, conn: Connection = Depends(connection),
                   viewer=Depends(session.ui_viewer)):
    return _report_page(request, conn, report_id, None, viewer)


@router.get("/ui/reports/{report_id}/v/{version}", response_class=HTMLResponse)
def exact_report(report_id: str, version: int, request: Request, conn: Connection = Depends(connection),
                 viewer=Depends(session.ui_viewer)):
    return _report_page(request, conn, report_id, version, viewer)


def _report_page(request: Request, conn: Connection, report_id: str, version: int | None, viewer=None,
                 *, error=None, feedback: str | None = None, status_code: int = 200):
    try:
        stored = reports.get(conn, report_id, version)
    except ReportError as exc:
        status = 404 if exc.envelope.code == "NOT_FOUND" else 503
        return templates.TemplateResponse(request, "error.html.j2", {"envelope": exc.envelope}, status_code=status)
    reader = request.app.state.status_reader
    history = [report_version(conn, r, reader) for r in reports.history(conn, report_id)]
    current = history[-1].version
    this = next(v for v in history if v.version == stored.record.version)
    review = _review(request, conn, report_id)
    reinvestigation, intents = None, {}
    if review is not None:  # F6 owns the cycle state (see observa_hitl.contracts)
        cycle = next((c for c in review.cycles if c.report_version == this.version), None)
        state = cycle.state if cycle else "pending_review"
        open_for_decision = state in DECIDABLE_STATES
        can_request = state in CHANGEABLE_STATES
        decisions = [d for d in review.decisions if d.version == this.version]
        intents = {i.decision_id: i for i in review.feedback_intents}
        if cycle is not None:
            reinvestigation = next((r for r in review.reinvestigations
                                    if r.review_cycle_id == cycle.review_cycle_id), None)
    else:
        cycle, decisions = None, []
        open_for_decision = can_request = this.review_status == "pending_review"
    return templates.TemplateResponse(request, "report.html.j2", {
        "content": stored.content, "record": stored.record, "this": this, "history": history,
        "is_current": this.version == current, "current_version": current,
        "can_review": this.version == current and open_for_decision,
        "can_request_changes": this.version == current and can_request,
        "reinvestigation": reinvestigation, "intents": intents,
        "actions_available": request.app.state.review_actions_available,
        "viewer_can_review": bool(viewer is not None and viewer.can_review),
        "cycle": cycle, "decisions": decisions, "review": review,
        "idempotency_key": str(uuid.uuid4()), "decision_error": error, "feedback_value": feedback,
        **_page_context(request, viewer),
    }, status_code=status_code)


def _review(request: Request, conn: Connection, report_id: str):
    if not request.app.state.f6:
        return None
    from ..api.reviews import gate_for
    return gate_for(request).summary(conn, report_id)


DECIDABLE_STATES = ("pending_review", "clarification_needed", "revision_failed", "limit_reached")
CHANGEABLE_STATES = ("pending_review", "clarification_needed")  # mirrors observa_hitl.contracts (F6 is optional)
_UI_STATUS = {"VALIDATION_ERROR": 422, "NOT_AUTHORIZED": 403, "VERSION_CONFLICT": 409, "IDEMPOTENCY_CONFLICT": 409,
              "NOT_FOUND": 404, "RETRY_LIMIT_REACHED": 409}


@router.post("/ui/reports/{report_id}/v/{version}/decide", response_class=HTMLResponse)
def decide(report_id: str, version: int, request: Request, decision: str = Form(""),
           feedback: str | None = Form(None), idempotency_key: str = Form(""), csrf_token: str | None = Form(None),
           viewer=Depends(session.ui_viewer)):
    if not request.app.state.review_actions_available:
        raise session.AuthFailure(403, "Review actions are not available on this service.")
    session.verify_csrf(request, csrf_token)
    from observa_hitl.contracts import DecisionCommand
    from observa_hitl.errors import F6Error
    from pydantic import ValidationError

    from ..api.reviews import submit
    try:
        cmd = DecisionCommand(report_id=report_id, version=version, decision=decision, feedback=feedback,
                              idempotency_key=idempotency_key)
        outcome = submit(request, viewer, cmd)
    except ValidationError:
        message, status = "The form was incomplete; reload the page and try again.", 422
    except F6Error as exc:
        message, status = exc.envelope.message, _UI_STATUS.get(exc.envelope.code, 503)
    else:  # post/redirect/get: a reload never re-submits; a double submit replays the same key
        return RedirectResponse(f"/ui/reports/{report_id}/v/{outcome.decision.version}#review", status_code=303)
    with request.app.state.engine.connect() as conn:
        return _report_page(request, conn, report_id, version, viewer, error=message, feedback=feedback,
                            status_code=status)
