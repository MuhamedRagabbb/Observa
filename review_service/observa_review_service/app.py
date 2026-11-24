"""FastAPI composition root. Run with:  uvicorn observa_review_service.app:create_app --factory"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine

from observa_reports.errors import ReportError

from .api import reports
from .db import make_engine
from .settings import Settings
from .views import StatusReader, default_status_reader, f6_status_reader

STATUS = {"NOT_FOUND": 404, "VALIDATION_ERROR": 422, "VERSION_CONFLICT": 409, "STORAGE_FAILURE": 503}


def create_app(settings: Settings | None = None, engine: Engine | None = None,
               status_reader: StatusReader | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="OBSERVA review service", version="0.1.0",
                  description="F7 incident reports and the F6 human review gate.")
    app.state.engine = engine or make_engine(settings.DATABASE_URL)
    app.state.status_reader = status_reader or default_status_reader()
    app.state.review_actions_available = f6_status_reader() is not None

    @app.exception_handler(ReportError)
    async def report_error(_request: Request, exc: ReportError) -> JSONResponse:
        return JSONResponse(exc.envelope.model_dump(mode="json"), status_code=STATUS.get(exc.envelope.code, 500))

    app.include_router(reports.router)
    from .ui import routes as ui_routes  # the UI reuses the API's view helpers
    app.include_router(ui_routes.router)
    return app
