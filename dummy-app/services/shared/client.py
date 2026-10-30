import asyncio
import time

import httpx
from opentelemetry.propagators.textmap import DefaultGetter
from opentelemetry.trace import SpanKind, Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from services.shared.errors import AppError
from services.shared.telemetry import request_id_var

PROPAGATOR = TraceContextTextMapPropagator()
GETTER = DefaultGetter()


class DependencyClient:
    def __init__(self, telemetry, settings, client=None):
        self.telemetry = telemetry
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.dependency_timeout, connect=1),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )

    async def request(
        self, dependency, base_url, method, route, *, body=None, template=None, retry=False, headers=None,
        with_status=False,
    ):  # fmt: skip
        labels = {"dependency": dependency, "http.method": method, "http.route": template or route}
        for attempt in range(2 if retry else 1):
            start = time.perf_counter()
            with self.telemetry.tracer.start_as_current_span(
                f"{method} {template or route}",
                kind=SpanKind.CLIENT,
                attributes=labels,
            ) as span:
                outgoing = {**(headers or {}), "x-request-id": request_id_var.get()}
                PROPAGATOR.inject(outgoing)
                self.telemetry.log(
                    "INFO",
                    "dependency_request_started",
                    "Calling dependency",
                    **labels,
                    retry_attempt=attempt,
                )
                try:
                    response = await self.client.request(
                        method, base_url + route, json=body, headers=outgoing
                    )
                    span.set_attribute("http.status_code", response.status_code)
                    self.telemetry.count(
                        "dependency.calls", {**labels, "http.status_code": response.status_code}
                    )
                    if response.status_code >= 400:
                        self.telemetry.count("dependency.failures", labels)
                        span.set_status(Status(StatusCode.ERROR, "DependencyRejected"))
                        self.telemetry.log(
                            "WARN" if response.status_code < 500 else "ERROR",
                            "dependency_request_failed",
                            "Dependency returned an error",
                            **labels,
                            **{"http.status_code": response.status_code, "error.type": "DependencyRejected"},
                            duration_ms=round((time.perf_counter() - start) * 1000),
                            timeout=False,
                            retry_attempt=attempt,
                        )
                        try:
                            error = response.json()
                            code, details = error["error_code"], error.get("details")
                        except (ValueError, KeyError, TypeError, AttributeError):
                            code, details = "dependency_error", None
                        raise AppError(
                            response.status_code, code, "Dependency could not complete the operation", details
                        )
                    self.telemetry.log(
                        "INFO",
                        "dependency_request_completed",
                        "Dependency request completed",
                        **labels,
                        **{"http.status_code": response.status_code},
                        duration_ms=round((time.perf_counter() - start) * 1000),
                        retry_attempt=attempt,
                    )
                    return (response.status_code, response.json()) if with_status else response.json()
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    timeout = isinstance(exc, httpx.TimeoutException)
                    self.telemetry.count("dependency.calls", {**labels, "http.status_code": 0})
                    self.telemetry.count("dependency.failures", labels)
                    if timeout:
                        self.telemetry.count("dependency.timeouts", labels)
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    # Record a sanitized exception; raw network errors can include secret URLs.
                    span.record_exception(RuntimeError(type(exc).__name__))
                    self.telemetry.log(
                        "ERROR",
                        "dependency_request_failed",
                        "Dependency transport unavailable",
                        **labels,
                        retry_attempt=attempt,
                        timeout=timeout,
                        duration_ms=round((time.perf_counter() - start) * 1000),
                        **{"error.type": type(exc).__name__},
                    )
                    if not retry or attempt == 1:
                        raise AppError(
                            504 if timeout else 502,
                            "dependency_timeout" if timeout else "dependency_unavailable",
                            "Dependency transport unavailable",
                        ) from None
                finally:
                    self.telemetry.duration("dependency.duration", time.perf_counter() - start, labels)
            await asyncio.sleep(0.05 * (attempt + 1))

    async def close(self):
        await self.client.aclose()
