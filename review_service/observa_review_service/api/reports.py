"""Read API over F7 report versions. Every error body is a contract ErrorEnvelope."""
from typing import Iterator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import Connection

from observa_reports.render import render_markdown
from observa_reports.service import ReportService

from ..views import report_version

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])
reports = ReportService()


def connection(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.connect() as conn:
        yield conn


def _document(conn, request, stored):
    return {"report_version": report_version(conn, stored.record, request.app.state.status_reader)
            .model_dump(mode="json"), "content": stored.content.model_dump(mode="json")}


@router.get("")
def list_reports(request: Request, conn: Connection = Depends(connection)) -> list[dict]:
    items = []
    for record in reports.catalog(conn):
        title = reports.get(conn, record.report_id, record.version).content.overview.title
        items.append({**report_version(conn, record, request.app.state.status_reader).model_dump(mode="json"),
                      "title": title})
    return items


@router.get("/{report_id}")
def current_version(report_id: str, request: Request, conn: Connection = Depends(connection)) -> dict:
    return _document(conn, request, reports.get(conn, report_id))


@router.get("/{report_id}/versions")
def versions(report_id: str, request: Request, conn: Connection = Depends(connection)) -> list[dict]:
    reports.get(conn, report_id)  # NOT_FOUND for an unknown report
    return [report_version(conn, r, request.app.state.status_reader).model_dump(mode="json")
            for r in reports.history(conn, report_id)]


@router.get("/{report_id}/versions/{version}")
def exact_version(report_id: str, version: int, request: Request, conn: Connection = Depends(connection)) -> dict:
    return _document(conn, request, reports.get(conn, report_id, version))


@router.get("/{report_id}/versions/{version}/report.md", response_class=PlainTextResponse)
def markdown(report_id: str, version: int, conn: Connection = Depends(connection)) -> PlainTextResponse:
    stored = reports.get(conn, report_id, version)
    return PlainTextResponse(render_markdown(stored.content, stored.record), media_type="text/markdown")
