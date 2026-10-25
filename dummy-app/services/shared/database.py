import asyncio
import time
from contextlib import asynccontextmanager

import asyncpg
from opentelemetry.metrics import Observation
from opentelemetry.trace import Status, StatusCode

from services.shared.errors import AppError


class Database:
    def __init__(self, settings, telemetry, runtime):
        self.settings, self.telemetry, self.runtime = settings, telemetry, runtime
        self.pool = None
        for state in ("active", "idle", "max"):
            telemetry.meter.create_observable_gauge(f"db.pool.{state}", [self._callback(state)])

    def _callback(self, state):
        def read(_):
            if self.pool is None:
                return [Observation(0)]
            idle = self.pool.get_idle_size()
            return [
                Observation(
                    {"idle": idle, "active": self.pool.get_size() - idle, "max": self.settings.pool_max}[
                        state
                    ]
                )
            ]

        return read

    async def open(self):
        for attempt in range(8):
            try:
                self.pool = await asyncpg.create_pool(
                    dsn=self.settings.dsn,
                    min_size=1,
                    max_size=self.settings.pool_max,
                    command_timeout=self.settings.query_timeout,
                    timeout=3,
                    server_settings={"application_name": self.telemetry.service},
                )
                return
            except (OSError, asyncpg.PostgresError):
                self.telemetry.log(
                    "WARN", "db_connect_retry", "Database connection unavailable", attempt=attempt + 1
                )
                if attempt == 7:
                    raise RuntimeError("Database startup connection failed") from None
                await asyncio.sleep(min(0.25 * 2**attempt, 3))

    @asynccontextmanager
    async def connection(self, operation, *, hold=False):
        start = time.perf_counter()
        attrs = {"db.system": "postgresql", "db.operation": operation, **self.runtime.attributes()}
        with self.telemetry.tracer.start_as_current_span("db.acquire", attributes=attrs) as span:
            try:
                connection = await self.pool.acquire(timeout=self.settings.pool_timeout)
            except TimeoutError:
                self.telemetry.count("db.pool.timeouts")
                self.telemetry.log(
                    "ERROR",
                    "db_pool_timeout",
                    "Database connection acquisition timed out",
                    operation=operation,
                    **{"error.type": "PoolAcquireTimeout"},
                )
                span.set_status(Status(StatusCode.ERROR, "PoolAcquireTimeout"))
                raise AppError(503, "db_pool_timeout", "Database connection capacity unavailable") from None
            finally:
                self.telemetry.duration("db.pool.acquire.duration", time.perf_counter() - start)
        try:
            with self.telemetry.tracer.start_as_current_span(f"db.{operation}", attributes=attrs) as span:
                start = time.perf_counter()
                try:
                    if hold:
                        await self.runtime.hold()
                    async with connection.transaction():
                        yield connection
                except BaseException as exc:
                    # Avoid asyncpg exceptions containing SQL values or connection credentials.
                    span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                    self.telemetry.log(
                        "WARN",
                        "db_transaction_rollback",
                        "Database operation rolled back",
                        operation=operation,
                        **{"error.type": type(exc).__name__},
                    )
                    if isinstance(exc, (asyncpg.PostgresError, TimeoutError, OSError)):
                        raise AppError(
                            503, "database_unavailable", "Database operation unavailable"
                        ) from None
                    raise
                finally:
                    elapsed = time.perf_counter() - start
                    self.telemetry.duration("db.query.duration", elapsed, {"db.operation": operation})
                    if elapsed > 0.25:
                        self.telemetry.log(
                            "WARN",
                            "db_query_slow",
                            "Database operation exceeded latency threshold",
                            operation=operation,
                            duration_ms=round(elapsed * 1000),
                        )
        finally:
            await self.pool.release(connection, timeout=3)

    async def ready(self):
        async with self.pool.acquire(timeout=0.2) as connection:
            await connection.fetchval("SELECT 1", timeout=0.2)

    async def close(self):
        if self.pool:
            try:
                await asyncio.wait_for(self.pool.close(), 5)
            except TimeoutError:
                self.pool.terminate()
