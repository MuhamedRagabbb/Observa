import asyncio
import json
import os
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.shared.errors import AppError
from services.shared.models import ERRORS
from services.shared.telemetry import utcnow
from services.shared.ui import register_ui

Scenario = Literal["db_connection_exhaustion", "payments_errors", "bad_deployment"]
TARGETS = {
    "db_connection_exhaustion": "orders-service",
    "payments_errors": "payments-service",
    "bad_deployment": "orders-service",
}
SIGNALS = {
    "db_connection_exhaustion": ["pool_saturation", "db_acquire_timeouts", "orders_latency"],
    "payments_errors": ["payment_failures", "orders_errors", "failed_payment_spans"],
    "bad_deployment": ["release_changed", "slow_order_persistence", "OrderPersistenceCompatibilityError"],
}


def operational(event, **fields):
    print(
        json.dumps(
            {
                "timestamp": utcnow(),
                "level": "INFO",
                "service.name": "fault-controller",
                "event": event,
                "message": event,
                "request_id": "system",
                **fields,
            }
        ),
        flush=True,
    )


class Activation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Scenario
    duration_seconds: float = Field(default=120, ge=0.1, le=1800)
    parameters: dict[str, float | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def parameters_valid(self):
        ranges = {
            "db_connection_exhaustion": {"hold_seconds": (0.1, 15)},
            "payments_errors": {"failure_rate": (0, 1), "status_code": (402, 503)},
            "bad_deployment": {"delay_seconds": (1, 2)},
        }[self.scenario]
        if self.parameters.keys() - ranges.keys():
            raise ValueError("Unknown scenario parameter")
        for key, value in self.parameters.items():
            if not isinstance(value, (float, int)) or isinstance(value, bool):
                raise ValueError("Parameters must be numeric")
            lo, hi = ranges[key]
            if not lo <= value <= hi or (key == "status_code" and value not in (402, 503)):
                raise ValueError("Scenario parameter out of range")
        return self

    def runtime(self, expires_at):
        values = {"expires_at": expires_at}
        if self.scenario == "db_connection_exhaustion":
            values["db_hold_seconds"] = self.parameters.get("hold_seconds", 8)
        elif self.scenario == "payments_errors":
            values["payment_failure_rate"] = self.parameters.get("failure_rate", 0.9)
            values["payment_failure_status"] = self.parameters.get("status_code", 503)
        else:
            values.update(
                release_version="v2-buggy",
                persistence_delay_seconds=self.parameters.get("delay_seconds", 1.5),
            )
        return values


class Deactivation(BaseModel):
    scenario: Scenario


class AutoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = 42
    scenarios: list[Scenario] = Field(default_factory=lambda: list(TARGETS), min_length=1, max_length=30)
    shuffle: bool = False
    warmup_seconds: float = Field(default=120, ge=0, le=3600)
    duration_seconds: float = Field(default=120, ge=0.1, le=1800)
    recovery_seconds: float = Field(default=90, ge=0, le=3600)
    repeat: int = Field(default=1, ge=1, le=1000)


class Incident(BaseModel):
    incident_id: str
    scenario: Scenario
    root_cause_service: str
    root_cause: str
    started_at: str
    ended_at: str | None
    expires_at: float
    parameters: dict[str, float | str]
    expected_signals: list[str]
    status: str
    acknowledged_at: str | None = None
    end_reason: str | None = None
    interrupted: bool = False


class ControllerStatus(BaseModel):
    active: Incident | None
    auto_running: bool


class ControllerHealth(BaseModel):
    status: Literal["ready"]


class Journal:
    """Evaluation-only file. Never mounted in an application or observability container."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records = []
        if self.path.exists():
            self.records = [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    def write(self):
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            for record in self.records:
                stream.write(json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def begin(self, config, expires_at):
        record = {
            "incident_id": f"inc-{uuid4()}",
            "scenario": config.scenario,
            "root_cause_service": TARGETS[config.scenario],
            "started_at": utcnow(),
            "ended_at": None,
            "expires_at": expires_at,
            "parameters": config.runtime(expires_at),
            "expected_signals": SIGNALS[config.scenario],
            "status": "activating",
            "root_cause": "Orders DB connection-pool exhaustion"
            if config.scenario == "db_connection_exhaustion"
            else config.scenario,
        }
        self.records.append(record)
        self.write()
        return record


class Controller:
    def __init__(self, path, client=None):
        self.journal = Journal(path)
        self.client = client or httpx.AsyncClient(timeout=3, limits=httpx.Limits(max_connections=4))
        self.lock = asyncio.Lock()
        self.stop_lock = asyncio.Lock()
        self.stopping = False
        self.active = None
        self.auto_task = None
        self.expiry_task = None
        self.urls = {
            "orders-service": os.getenv("ORDERS_CONTROL_URL", "http://orders:8001"),
            "payments-service": os.getenv("PAYMENTS_CONTROL_URL", "http://payments:8003"),
        }

    def status(self):
        return {
            "active": self.active,
            "auto_running": self.stopping or (self.auto_task is not None and not self.auto_task.done()),
        }

    async def send(self, scenario, settings=None):
        url = self.urls[TARGETS[scenario]] + "/_control/runtime"
        try:
            response = await self.client.request(
                "PUT" if settings else "DELETE",
                url,
                json=settings,
                headers={"x-control-token": os.getenv("CONTROL_TOKEN", "local-control-only")},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            raise AppError(
                503,
                "target_unavailable",
                "Target did not acknowledge control; its lease still expires automatically",
            ) from None

    async def activate(self, config, *, automatic=False):
        async with self.lock:
            if not automatic and self.status()["auto_running"]:
                raise AppError(409, "auto_running", "Stop the automatic schedule before manual activation")
            if self.active:
                raise AppError(
                    409, "fault_conflict", "Deactivate the current incident before activating another"
                )
            expires_at = time.time() + config.duration_seconds
            self.active = self.journal.begin(config, expires_at)
            try:
                await self.send(config.scenario, config.runtime(expires_at))
            except AppError:
                self.active["status"] = "activation_unconfirmed"
                self.journal.write()
                raise
            self.active["status"] = "active"
            self.active["acknowledged_at"] = utcnow()
            self.journal.write()
            operational(
                "activation_acknowledged", incident_id=self.active["incident_id"], scenario=config.scenario
            )
            return self.status()

    async def _deactivate_locked(self, reason):
        if not self.active:
            return
        await self.send(self.active["scenario"])
        self.finish(reason)

    def finish(self, reason):
        self.active.update(ended_at=utcnow(), status="ended", end_reason=reason)
        self.journal.write()
        operational("deactivation_completed", incident_id=self.active["incident_id"], reason=reason)
        self.active = None

    async def deactivate(self, scenario, *, automatic=False):
        async with self.lock:
            if not automatic and self.status()["auto_running"]:
                raise AppError(409, "auto_running", "Stop the automatic schedule before manual deactivation")
            if self.active and self.active["scenario"] == scenario:
                await self._deactivate_locked("manual" if not automatic else "schedule")
            return self.status()

    async def expire(self):
        while True:
            await asyncio.sleep(0.05)
            async with self.lock:
                if self.active and time.time() >= self.active["expires_at"]:
                    try:
                        await self._deactivate_locked("ttl")
                    except AppError:
                        # Expiration is enforced at target too, even without a control acknowledgement.
                        self.finish("target_lease_expired")

    async def start_auto(self, config):
        async with self.lock:
            if self.active or self.status()["auto_running"]:
                raise AppError(409, "controller_busy", "Stop the current schedule or incident first")
            self.auto_task = asyncio.create_task(self.run_auto(config))
            return self.status()

    async def run_auto(self, config):
        try:
            await asyncio.sleep(config.warmup_seconds)
            order = config.scenarios.copy()
            rng = random.Random(config.seed)
            for _ in range(config.repeat):
                if config.shuffle:
                    rng.shuffle(order)
                for scenario in order:
                    await self.activate(
                        Activation(scenario=scenario, duration_seconds=config.duration_seconds),
                        automatic=True,
                    )
                    await asyncio.sleep(config.duration_seconds)
                    await self.deactivate(scenario, automatic=True)
                    await asyncio.sleep(config.recovery_seconds)
        except AppError:
            print(
                json.dumps(
                    {
                        "timestamp": utcnow(),
                        "level": "ERROR",
                        "service.name": "fault-controller",
                        "event": "schedule_failed",
                        "message": "Target control unavailable",
                    }
                ),
                flush=True,
            )
        finally:
            async with self.lock:
                if self.active:
                    try:
                        await self._deactivate_locked("auto_stopped")
                    except AppError:
                        pass

    async def stop_auto(self):
        async with self.stop_lock:
            async with self.lock:
                self.stopping = True
                task = self.auto_task
            try:
                if task and not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                async with self.lock:
                    self.auto_task = None
                    if self.active:
                        await self._deactivate_locked("auto_stopped")
            finally:
                async with self.lock:
                    self.stopping = False
        return self.status()

    async def open(self):
        # A previous process might have died after activation. Clear targets before accepting commands.
        for scenario in ("db_connection_exhaustion", "payments_errors"):
            await self.send(scenario)
        for record in self.journal.records:
            if record["ended_at"] is None:
                record.update(
                    ended_at=utcnow(), status="ended", end_reason="controller_restart", interrupted=True
                )
        self.journal.write()
        self.expiry_task = asyncio.create_task(self.expire())
        operational("controller_started")

    async def close(self):
        try:
            await self.stop_auto()
        except AppError:
            pass
        if self.expiry_task:
            self.expiry_task.cancel()
            await asyncio.gather(self.expiry_task, return_exceptions=True)
        await self.client.aclose()
        operational("controller_stopped")


def create_app(*, path=None, client=None):
    controller = Controller(path or os.getenv("GROUND_TRUTH_PATH", "/ground-truth/incidents.jsonl"), client)

    @asynccontextmanager
    async def lifespan(app):
        await controller.open()
        try:
            yield
        finally:
            await controller.close()

    app = FastAPI(title="Evaluation-only fault controller", lifespan=lifespan, responses=ERRORS)
    register_ui(app, "control")
    app.state.controller = controller

    @app.exception_handler(AppError)
    async def error(request, exc):
        return JSONResponse(
            {"error_code": exc.code, "message": exc.message, "request_id": f"ctl-{uuid4()}"},
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        return await error(request, AppError(422, "validation_error", "Invalid controller configuration"))

    @app.get("/health", response_model=ControllerHealth)
    async def health():
        return {"status": "ready"}

    @app.get("/faults/status", response_model=ControllerStatus)
    async def status():
        return controller.status()

    @app.post("/faults/activate", response_model=ControllerStatus)
    async def activate(config: Activation):
        return await controller.activate(config)

    @app.post("/faults/deactivate", response_model=ControllerStatus)
    async def deactivate(config: Deactivation):
        return await controller.deactivate(config.scenario)

    @app.post("/faults/auto/start", response_model=ControllerStatus)
    async def start_auto(config: AutoConfig):
        return await controller.start_auto(config)

    @app.post("/faults/auto/stop", response_model=ControllerStatus)
    async def stop_auto():
        return await controller.stop_auto()

    return app
