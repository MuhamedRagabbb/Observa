"""FastAPI composition root. Run with:  uvicorn observa_review_service.app:create_app --factory"""
from importlib.util import find_spec
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import Engine

from observa_investigation.contracts import ErrorEnvelope
from observa_reports.errors import ReportError

from .api import reports
from .db import make_engine
from .settings import Settings
from .ui import session
from .views import StatusReader, f6_status_reader, pending_only

STATUS = {"NOT_FOUND": 404, "VALIDATION_ERROR": 422, "VERSION_CONFLICT": 409, "STORAGE_FAILURE": 503,
          "IDEMPOTENCY_CONFLICT": 409, "NOT_AUTHORIZED": 403, "RETRY_LIMIT_REACHED": 409}


def f6_installed() -> bool:
    return find_spec("observa_hitl") is not None


def _load_reviewers(settings: Settings):
    path = Path(settings.REVIEWERS_FILE)
    if not path.is_file():
        return None
    from observa_hitl.auth import ReviewerDirectory
    return ReviewerDirectory.from_file(path)


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               status_reader: StatusReader | None = None, *, reviewers=None, session_secret: str | None = None,
               f6: bool | None = None) -> FastAPI:
    """`f6=None` detects the optional F6 package; `f6=False` runs the F7-only read side."""
    settings = settings or Settings()
    f6 = f6_installed() if f6 is None else f6
    f6_reader = f6_status_reader() if f6 else None
    app = FastAPI(title="OBSERVA review service", version="0.1.0",
                  description="F7 incident reports and the F6 human review gate.")
    app.state.settings = settings
    app.state.engine = engine or make_engine(settings.DATABASE_URL)
    app.state.f6 = f6
    app.state.status_reader = status_reader or f6_reader or pending_only
    app.state.reviewers = (reviewers if reviewers is not None else _load_reviewers(settings)) if f6 else None
    app.state.session_signer = session.SessionSigner(session_secret or settings.SESSION_SECRET)
    app.state.review_actions_available = f6 and app.state.reviewers is not None

    @app.exception_handler(ReportError)
    async def report_error(_request: Request, exc: ReportError) -> JSONResponse:
        return JSONResponse(exc.envelope.model_dump(mode="json"), status_code=STATUS.get(exc.envelope.code, 500))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        envelope = ErrorEnvelope(code="VALIDATION_ERROR", message="The request is not valid.", retryable=False,
                                 operation_id=f"{request.method} {request.url.path}",
                                 details={"errors": jsonable_encoder(exc.errors(), exclude={"ctx", "input"})})
        return JSONResponse(envelope.model_dump(mode="json"), status_code=422)

    @app.exception_handler(session.AuthFailure)
    async def auth_failure(_request: Request, exc: session.AuthFailure) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(exc.envelope.model_dump(mode="json"), status_code=exc.status, headers=headers)

    @app.exception_handler(session.LoginRequired)
    async def login_required(request: Request, _exc: session.LoginRequired) -> RedirectResponse:
        return RedirectResponse(f"/ui/login?next={request.url.path}", status_code=303)

    app.include_router(reports.router, dependencies=[Depends(session.read_access)])
    if f6:
        from observa_hitl.errors import F6Error

        from .api import internal, reviews

        @app.exception_handler(F6Error)
        async def f6_error(_request: Request, exc: F6Error) -> JSONResponse:
            return JSONResponse(exc.envelope.model_dump(mode="json"), status_code=STATUS.get(exc.envelope.code, 500))

        app.include_router(reviews.router)
        app.include_router(internal.router)
    from .ui import routes as ui_routes  # the UI reuses the API's view helpers
    app.include_router(ui_routes.router)
    return app
