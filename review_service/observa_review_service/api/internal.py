"""Internal service endpoints (Bearer service token whose sha256 is SERVICE_TOKEN_SHA256; closed when unset).

    POST /internal/v1/reports                                  F7 publish intake: v1, or vN+1 for an upstream update
    POST /internal/v1/reports/{report_id}/revisions            F2's revised findings for a reinvestigating cycle
    POST /internal/v1/reinvestigations/{request_id}/failed     F2 could not revise: vN stays current
    POST /internal/v1/outbox/{event_id}/retry                  requeue a failed outbox event
"""
import hashlib
import hmac

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from observa_hitl.feedback import handle_revision_failed, handle_revision_ready
from observa_hitl.outbox import Outbox
from observa_reports.errors import ReportError
from observa_reports.inputs import ReportInputs
from observa_reports.service import ReportService, RevisionRef
from observa_reports.tables import report_versions

from ..db import unit_of_work
from ..ui import session


def require_service(request: Request, authorization: str | None = Header(None)) -> None:
    expected = request.app.state.settings.SERVICE_TOKEN_SHA256
    scheme, _, token = (authorization or "").partition(" ")
    presented = hashlib.sha256(token.strip().encode()).hexdigest()
    if not expected or scheme.lower() != "bearer" or not token.strip() \
            or not hmac.compare_digest(expected.lower(), presented):
        raise session.AuthFailure(401, "A valid service token is required.", operation_id="internal")


router = APIRouter(prefix="/internal/v1", tags=["internal"], dependencies=[Depends(require_service)])


class RevisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    review_cycle_id: str = Field(min_length=1)
    inputs: ReportInputs


class FailureBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error: str = Field(min_length=1, max_length=1000)


def _gate(request: Request):
    from .reviews import gate_for
    return gate_for(request)


def _record(record, status: int) -> JSONResponse:
    return JSONResponse(record.model_dump(mode="json"), status_code=status)


@router.post("/reports", status_code=201)
def publish(inputs: ReportInputs, request: Request) -> JSONResponse:
    """Publish a report version and open its review cycle. An identical republish is 200 with the same
    version; new content for an existing report becomes the next version (CAS on the current head)."""
    reports = ReportService()
    with unit_of_work(request.app.state.engine) as conn:
        before = conn.execute(select(func.count()).select_from(report_versions)).scalar_one()
        try:
            record = reports.publish(conn, inputs)
        except ReportError as exc:
            current = (exc.envelope.details or {}).get("current_version")
            if exc.envelope.code != "VERSION_CONFLICT" or current is None:
                raise
            record = reports.publish(conn, inputs, revision_of=RevisionRef(review_cycle_id=None,
                                                                           expected_previous=current))
        _gate(request).open(conn, record.report_id, record.version)
        created = conn.execute(select(func.count()).select_from(report_versions)).scalar_one() > before
    return _record(record, 201 if created else 200)


@router.post("/reports/{report_id}/revisions", status_code=201)
def revision(report_id: str, body: RevisionBody, request: Request) -> JSONResponse:
    with unit_of_work(request.app.state.engine) as conn:
        before = conn.execute(select(func.count()).select_from(report_versions)).scalar_one()
        record = handle_revision_ready(conn, report_id, body.inputs, body.review_cycle_id, gate=_gate(request),
                                       reports=ReportService())
        created = conn.execute(select(func.count()).select_from(report_versions)).scalar_one() > before
    return _record(record, 201 if created else 200)


@router.post("/reinvestigations/{request_id}/failed")
def revision_failed(request_id: str, body: FailureBody, request: Request) -> dict:
    with unit_of_work(request.app.state.engine) as conn:
        handle_revision_failed(conn, request_id, body.error)
    return {"request_id": request_id, "status": "failed"}


@router.post("/outbox/{event_id}/retry")
def retry_event(event_id: str, request: Request) -> dict:
    with unit_of_work(request.app.state.engine) as conn:
        Outbox().retry(conn, event_id)
    return {"event_id": event_id, "status": "pending"}
