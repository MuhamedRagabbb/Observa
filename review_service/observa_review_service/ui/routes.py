"""Reviewer dashboard routes. Read-only in F7; Approve / Request changes are wired by F6 (Phase 4).

Every template value is autoescaped. Narrative text goes through markdown-it with raw
HTML disabled, so upstream (LLM-written) claims can format text but never inject markup.
"""
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlalchemy import Connection

from observa_reports.errors import ReportError
from observa_reports.service import ReportService

from ..views import report_version

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


@router.get("/ui", response_class=HTMLResponse)
def index(request: Request, conn: Connection = Depends(connection)):
    cards = []
    for record in reports.catalog(conn):
        stored = reports.get(conn, record.report_id, record.version)
        cards.append({"version": report_version(conn, record, request.app.state.status_reader),
                      "content": stored.content})
    return templates.TemplateResponse(request, "index.html.j2", {"cards": cards})


@router.get("/ui/reports/{report_id}", response_class=HTMLResponse)
def current_report(report_id: str, request: Request, conn: Connection = Depends(connection)):
    return _report_page(request, conn, report_id, None)


@router.get("/ui/reports/{report_id}/v/{version}", response_class=HTMLResponse)
def exact_report(report_id: str, version: int, request: Request, conn: Connection = Depends(connection)):
    return _report_page(request, conn, report_id, version)


def _report_page(request: Request, conn: Connection, report_id: str, version: int | None):
    try:
        stored = reports.get(conn, report_id, version)
    except ReportError as exc:
        status = 404 if exc.envelope.code == "NOT_FOUND" else 503
        return templates.TemplateResponse(request, "error.html.j2", {"envelope": exc.envelope}, status_code=status)
    reader = request.app.state.status_reader
    history = [report_version(conn, r, reader) for r in reports.history(conn, report_id)]
    current = history[-1].version
    this = next(v for v in history if v.version == stored.record.version)
    return templates.TemplateResponse(request, "report.html.j2", {
        "content": stored.content, "record": stored.record, "this": this, "history": history,
        "is_current": this.version == current, "current_version": current,
        "can_review": this.version == current and this.review_status == "pending_review",
        "actions_available": request.app.state.review_actions_available,
    })
