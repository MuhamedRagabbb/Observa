import asyncio
import re
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.exceptions import HTTPException

from services.shared.client import PROPAGATOR
from services.shared.errors import AppError
from services.shared.models import ERRORS, HealthResult, RuntimeUpdate
from services.shared.runtime import Runtime
from services.shared.telemetry import Telemetry, correlation, request_id_var
from services.shared.ui import is_untraced_page


class ObservabilityMiddleware:
    def __init__(self, app, telemetry, runtime):
        self.app, self.telemetry, self.runtime = app, telemetry, runtime

    async def __call__(self, scope, receive, send):
        # Not business telemetry: health probes, the favicon, controller internals, /ui/ files
        # and the pages this app serves (GET/HEAD on its allowlist; see services/shared/ui.py).
        # Everything else, including unknown paths such as /unavailable, is recorded.
        if (
            scope["type"] != "http"
            or scope["path"] in ("/health", "/favicon.ico")
            or scope["path"].startswith(("/_control", "/ui/"))
            or is_untraced_page(scope["app"], scope["method"], scope["path"])
        ):
            return await self.app(scope, receive, send)
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        supplied = headers.get("x-request-id", "")
        request_id = supplied if re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", supplied) else f"req-{uuid4()}"
        token = request_id_var.set(request_id)
        started, status = time.perf_counter(), 500
        with self.telemetry.tracer.start_as_current_span(
            scope["method"],
            context=PROPAGATOR.extract(headers),
            kind=SpanKind.SERVER,
            attributes={"http.method": scope["method"], **self.runtime.attributes()},
        ) as span:
            self.telemetry.log(
                "INFO", "request_received", "HTTP request received", **{"http.method": scope["method"]}
            )

            async def traced_send(message):
                nonlocal status
                if message["type"] == "http.response.start":
                    status = message["status"]
                    message["headers"] += [
                        (b"x-request-id", request_id.encode()),
                        (b"x-trace-id", correlation()["trace_id"].encode()),
                    ]
                await send(message)

            try:
                await self.app(scope, receive, traced_send)
            except Exception as exc:
                self.telemetry.log(
                    "ERROR",
                    "unexpected_exception",
                    "Unhandled request failure",
                    **{"error.type": type(exc).__name__},
                )
                span.record_exception(RuntimeError(type(exc).__name__))
                response = error_response(AppError(500, "internal_error", "Unexpected service error"))
                await response(scope, receive, traced_send)
            finally:
                route = getattr(scope.get("route"), "path", "unmatched")
                attrs = {"http.method": scope["method"], "http.route": route, "http.status_code": status}
                span.update_name(f"{scope['method']} {route}")
                span.set_attributes(attrs)
                if status >= 400:
                    span.set_status(Status(StatusCode.ERROR, f"HTTP {status}"))
                elapsed = time.perf_counter() - started
                self.telemetry.count("http.server.requests", attrs)
                if status >= 400:
                    self.telemetry.count("http.server.errors", attrs)
                self.telemetry.duration("http.server.duration", elapsed, attrs)
                self.telemetry.log(
                    "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO",
                    "request_completed",
                    "HTTP request completed",
                    **attrs,
                    duration_ms=round(elapsed * 1000),
                    **self.runtime.attributes(),
                )
                request_id_var.reset(token)


def error_response(exc):
    content = {
        "error_code": exc.code,
        "message": exc.message,
        "request_id": request_id_var.get(),
        "trace_id": correlation()["trace_id"],
    }
    if exc.details is not None:
        content["details"] = exc.details  # additive; legacy errors keep the original four fields
    return JSONResponse(status_code=exc.status, content=content)


def make_app(service, settings, *, repository_factory=None, telemetry=None, background=None):
    """``background(app)``: optional async context entered once the database is open (for
    service-owned background work) and exited before it closes."""
    telemetry = telemetry or Telemetry(service, settings)
    runtime = Runtime(telemetry)

    @asynccontextmanager
    async def lifespan(app):
        if repository_factory:
            db, repository = repository_factory(runtime, telemetry)
            app.state.db, app.state.repository = db, repository
            await db.open()
        telemetry.log("INFO", "service_started", "Service is ready", **runtime.attributes())
        try:
            if background:
                async with background(app):
                    yield
            else:
                yield
        finally:
            await runtime.close()
            if getattr(app.state, "client", None):
                await app.state.client.close()
            if repository_factory:
                await app.state.db.close()
            telemetry.log("INFO", "service_stopped", "Service shut down gracefully")
            await asyncio.to_thread(telemetry.close)

    app = FastAPI(title=service, version="1.0.0", lifespan=lifespan, responses=ERRORS)
    app.state.telemetry, app.state.runtime = telemetry, runtime
    app.add_middleware(ObservabilityMiddleware, telemetry=telemetry, runtime=runtime)

    @app.exception_handler(AppError)
    async def app_error(request, exc):
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        telemetry.log("WARN", "validation_failed", "Request did not match the API schema")
        return error_response(AppError(422, "validation_error", "Request did not match the API schema"))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(AppError(exc.status_code, "http_error", "Requested operation unavailable"))

    @app.get("/health", response_model=HealthResult, responses={503: {"model": HealthResult}})
    async def health(request: Request):
        try:
            if repository_factory:
                await request.app.state.db.ready()
        except Exception:
            telemetry.log("WARN", "readiness_failed", "Database readiness check failed")
            return JSONResponse(
                {"status": "unavailable", "service": service, **runtime.attributes()}, status_code=503
            )
        return {"status": "ready", "service": service, **runtime.attributes()}

    @app.put("/_control/runtime", include_in_schema=False)
    async def update_runtime(data: RuntimeUpdate, x_control_token: str = Header(default="")):
        if x_control_token != settings.control_token:
            raise AppError(401, "unauthorized", "Controller authentication required")
        if data.expires_at > time.time() + 1801:
            raise AppError(422, "invalid_expiry", "Maximum lease duration is 1800 seconds")
        await runtime.update(data)
        return {"status": "accepted"}

    @app.delete("/_control/runtime", include_in_schema=False)
    async def clear_runtime(x_control_token: str = Header(default="")):
        if x_control_token != settings.control_token:
            raise AppError(401, "unauthorized", "Controller authentication required")
        await runtime.update(None)
        return {"status": "cleared"}

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            for path, operations in schema["paths"].items():
                if path == "/health":
                    continue
                for operation in operations.values():
                    for response in operation.get("responses", {}).values():
                        response["headers"] = {
                            "X-Request-ID": {
                                "schema": {"type": "string"},
                                "description": "Request correlation ID, preserved across dependencies",
                            },
                            "X-Trace-ID": {
                                "schema": {"type": "string", "pattern": "^[a-f0-9]{32}$"},
                                "description": "End-to-end W3C trace ID",
                            },
                        }
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi

    return app
