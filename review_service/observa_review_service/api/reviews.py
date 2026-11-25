"""F6 review API: decisions and the review summary (internal service endpoints live in internal.py).

Every decision goes through ReviewGate.submit: one unit of work for the decision and its outbox
event, and an audit row (in its own transaction) for a rejected attempt.
"""
from typing import Iterator, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Connection

from observa_hitl.contracts import DecisionCommand
from observa_hitl.errors import F6Error
from observa_hitl.gate import ReviewGate

from ..db import unit_of_work
from ..ui import session

router = APIRouter(prefix="/api/v1/reports", tags=["reviews"])


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "request_changes"]
    feedback: str | None = None


def gate_for(request: Request) -> ReviewGate:
    gate = getattr(request.app.state, "review_gate", None)
    if gate is None:
        gate = request.app.state.review_gate = ReviewGate(
            max_review_cycles=request.app.state.settings.MAX_REVIEW_CYCLES)
    return gate


def submit(request: Request, reviewer, cmd: DecisionCommand):
    engine = request.app.state.engine
    return gate_for(request).submit(lambda: unit_of_work(engine), reviewer, cmd)


def connection(request: Request) -> Iterator[Connection]:
    with request.app.state.engine.connect() as conn:
        yield conn


@router.post("/{report_id}/versions/{version}/decisions", status_code=201)
def decide(report_id: str, version: int, body: DecisionBody, request: Request,
           idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
           reviewer=Depends(session.require_viewer)):
    if not idempotency_key or not idempotency_key.strip():
        raise F6Error("VALIDATION_ERROR", "The Idempotency-Key header is required.",
                      operation_id=f"decide:{report_id}:v{version}", details={"header": "Idempotency-Key"})
    cmd = DecisionCommand(report_id=report_id, version=version, decision=body.decision, feedback=body.feedback,
                          idempotency_key=idempotency_key.strip())
    outcome = submit(request, reviewer, cmd)
    return JSONResponse(outcome.decision.model_dump(mode="json"), status_code=200 if outcome.replayed else 201)


@router.get("/{report_id}/review")
def review_summary(report_id: str, request: Request, conn: Connection = Depends(connection),
                   _viewer=Depends(session.read_access)) -> dict:
    return gate_for(request).summary(conn, report_id).model_dump(mode="json")
