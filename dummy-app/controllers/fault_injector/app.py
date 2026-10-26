import asyncio
import json
import os
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import httpx
from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from controllers.fault_injector.health import HealthSnapshot, default_health
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


# Journal fields that are evaluation ground truth: never returned by an operator-facing API.
GROUND_TRUTH_FIELDS = frozenset({"root_cause", "root_cause_service", "expected_signals"})


class OperatorIncident(BaseModel):
    """The active incident as the operator sees it: what was asked for, not what it causes."""

    incident_id: str
    scenario: Scenario
    status: str
    started_at: str
    acknowledged_at: str | None = None
    expires_at: float
    parameters: dict[str, float | str]  # as requested; the runtime flags stay in the journal


OPERATOR_INCIDENT_FIELDS = tuple(OperatorIncident.model_fields)
assert not GROUND_TRUTH_FIELDS & set(OPERATOR_INCIDENT_FIELDS)


UNCONFIRMED = frozenset({"activation_unconfirmed", "deactivation_unconfirmed"})
# Why a control is not safe in a given state (the UI shows these; the server enforces its own locks).
BLOCKED_BY = {
    "manual": "fault_active", "automatic": "auto_running", "unknown": "status_unknown", "recovering": "recovering",
}  # fmt: skip


class Control(BaseModel):
    enabled: bool
    reason: str | None = None


class LastFault(BaseModel):
    scenario: Scenario
    ended_at: str
    end_reason: str


class AutoStatus(BaseModel):
    phase: Literal["warmup", "fault", "recovery", "stopping"]
    repeat: int  # 1-based pass over the scenario list
    repeats: int
    step: int  # 1-based fault in the whole run; 0 during warmup
    steps: int
    scenario: Scenario | None
    phase_started_at: float
    phase_ends_at: float | None  # None while stopping
    seed: int
    shuffle: bool
    config: AutoConfig


class ControllerStatus(BaseModel):
    state: Literal["idle", "manual", "automatic", "recovering", "unknown"]
    active: OperatorIncident | None
    auto_running: bool
    auto: AutoStatus | None
    last_fault: LastFault | None
    controls: dict[str, Control]
    now: float  # server clock, so countdowns do not depend on the browser's clock


class ActivityEntry(BaseModel):
    model_config = ConfigDict(extra="allow")  # event-specific fields (scenario, phase, reason...)
    at: str
    event: str


class Activity(BaseModel):
    capacity: int
    entries: list[ActivityEntry]


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


class ActivityLog:
    """Bounded, in-memory session log of control actions and state changes for the operator.

    Holds only what the operator asked for and what happened to it: never ground truth.
    """

    def __init__(self, capacity):
        self.capacity, self.entries = capacity, deque(maxlen=capacity)

    def record(self, event, **fields):
        self.entries.append({"at": utcnow(), "event": event, **fields})

    def view(self, limit=None):
        newest_first = list(reversed(self.entries))
        return {"capacity": self.capacity, "entries": newest_first[:limit] if limit else newest_first}


class Controller:
    def __init__(
        self,
        path,
        client=None,
        *,
        health=None,
        health_interval=5.0,
        recovery_limit=180.0,
        activity_capacity=200,
    ):
        """``health`` (a HealthMonitor) is checked every ``health_interval`` seconds. After a fault
        ends, the controller reports ``recovering`` until a later check finds every service healthy,
        or ``recovery_limit`` seconds pass (so missing metrics never lock the controls for good)."""
        self.journal = Journal(path)
        self.log = ActivityLog(activity_capacity)
        self.health, self.health_interval, self.recovery_limit = health, health_interval, recovery_limit
        self.health_task = None
        self.latest_health = None  # last HealthMonitor snapshot
        self.health_checked_at = None  # when that check started (epoch seconds)
        self.fault_ended_at = None
        self.recovery_announced = False
        self.requested_parameters = None  # what the operator asked for, for the active fault
        self.client = client or httpx.AsyncClient(timeout=3, limits=httpx.Limits(max_connections=4))
        self.lock = asyncio.Lock()
        self.stop_lock = asyncio.Lock()
        self.stopping = False
        self.active = None
        self.last_fault = None
        self.auto = None  # current phase of the automatic cycle
        self.auto_task = None
        self.expiry_task = None
        self.urls = {
            "orders-service": os.getenv("ORDERS_CONTROL_URL", "http://orders:8001"),
            "payments-service": os.getenv("PAYMENTS_CONTROL_URL", "http://payments:8003"),
        }

    def status(self):
        """Operator-facing state; the journal record keeps the ground truth fields."""
        active = None
        if self.active:
            active = {key: self.active.get(key) for key in OPERATOR_INCIDENT_FIELDS}
            active["parameters"] = self.requested_parameters
        auto_running = self.stopping or (self.auto_task is not None and not self.auto_task.done())
        state = self._state(auto_running)
        return {
            "state": state,
            "active": active,
            "auto_running": auto_running,
            "auto": dict(self.auto) if self.auto and auto_running else None,
            "last_fault": self.last_fault,
            "controls": self._controls(state, auto_running),
            "now": time.time(),
        }

    def _state(self, auto_running):
        if self.active and self.active["status"] in UNCONFIRMED:
            return "unknown"  # a control was not acknowledged: the fault may or may not be running
        if auto_running:
            return "automatic"
        if self.active:
            return "manual"
        return "recovering" if self._recovering() else "idle"

    def _recovering(self):
        if self.health is None or self.fault_ended_at is None:
            return False
        if time.time() - self.fault_ended_at >= self.recovery_limit:
            return False
        if self.latest_health is None or self.health_checked_at < self.fault_ended_at:
            return True  # not checked since the fault ended
        return not self._all_healthy()

    def _all_healthy(self):
        return all(row["state"] == "healthy" for row in self.latest_health["services"])

    def health_snapshot(self):
        if self.latest_health is None:
            return {"checked_at": None, "metrics": "unknown", "services": []}
        return self.latest_health

    async def watch_health(self):
        while True:
            started = time.time()
            try:
                snapshot = await self.health.check()
            except Exception as exc:  # keep watching; the last snapshot simply ages
                operational("health_check_failed", **{"error.type": type(exc).__name__})
            else:
                self._log_health_changes(snapshot)
                self.latest_health, self.health_checked_at = snapshot, started
                if self.fault_ended_at and not self.recovery_announced and not self._recovering():
                    self.recovery_announced = True
                    if started >= self.fault_ended_at and self._all_healthy():
                        self.log.record("services_recovered", scenario=self.last_fault["scenario"])
            await asyncio.sleep(self.health_interval)

    def _log_health_changes(self, snapshot):
        previous = {row["service"]: row["state"] for row in (self.latest_health or {}).get("services", [])}
        for row in snapshot["services"]:
            if previous.get(row["service"]) != row["state"]:
                self.log.record(
                    "service_health_changed",
                    service=row["service"],
                    previous=previous.get(row["service"]),
                    state=row["state"],
                )

    @staticmethod
    def _controls(state, auto_running):
        def control(enabled, reason):
            return {"enabled": enabled, "reason": None if enabled else reason}

        blocked = BLOCKED_BY.get(state)
        stoppable = state in ("manual", "unknown") and not auto_running
        return {
            "activate": control(state == "idle", blocked),
            "auto_start": control(state == "idle", blocked),
            "deactivate": control(stoppable, "auto_running" if auto_running else "no_active_fault"),
            "auto_stop": control(auto_running, "auto_not_running"),
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

    def activity(self, limit=None):
        return self.log.view(limit)

    def _reject(self, action, code, message):
        self.log.record("control_rejected", action=action, error_code=code)
        raise AppError(409, code, message)

    async def activate(self, config, *, automatic=False):
        async with self.lock:
            if not automatic and self.status()["auto_running"]:
                self._reject(
                    "activate", "auto_running", "Stop the automatic schedule before manual activation"
                )
            if self.active:
                self._reject(
                    "activate", "fault_conflict", "Deactivate the current incident before activating another"
                )
            self.log.record(
                "fault_requested",
                scenario=config.scenario,
                duration_seconds=config.duration_seconds,
                parameters=config.parameters,
                automatic=automatic,
            )
            expires_at = time.time() + config.duration_seconds
            self.active = self.journal.begin(config, expires_at)
            self.requested_parameters = dict(config.parameters)
            try:
                await self.send(config.scenario, config.runtime(expires_at))
            except AppError:
                self.active["status"] = "activation_unconfirmed"
                self.journal.write()
                self.log.record("activation_unconfirmed", scenario=config.scenario)
                raise
            self.log.record("fault_active", scenario=config.scenario)
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
        self.log.record("stop_requested", scenario=self.active["scenario"], reason=reason)
        try:
            await self.send(self.active["scenario"])
        except AppError:
            self.active["status"] = "deactivation_unconfirmed"  # still expires at the target's lease
            self.journal.write()
            self.log.record("stop_unconfirmed", scenario=self.active["scenario"])
            raise
        self.finish(reason)

    def finish(self, reason):
        self.active.update(ended_at=utcnow(), status="ended", end_reason=reason)
        self.journal.write()
        self.fault_ended_at, self.recovery_announced = time.time(), False
        self.last_fault = {
            "scenario": self.active["scenario"],
            "ended_at": self.active["ended_at"],
            "end_reason": reason,
        }
        operational("deactivation_completed", incident_id=self.active["incident_id"], reason=reason)
        self.log.record("fault_stopped", scenario=self.active["scenario"], reason=reason)
        self.active = None

    async def deactivate(self, scenario, *, automatic=False):
        async with self.lock:
            if not automatic and self.status()["auto_running"]:
                self._reject(
                    "deactivate", "auto_running", "Stop the automatic schedule before manual deactivation"
                )
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
                self._reject("auto_start", "controller_busy", "Stop the current schedule or incident first")
            self.log.record(
                "cycle_started",
                repeats=config.repeat,
                seed=config.seed,
                shuffle=config.shuffle,
                scenarios=config.scenarios,
                warmup_seconds=config.warmup_seconds,
                duration_seconds=config.duration_seconds,
                recovery_seconds=config.recovery_seconds,
            )
            self.auto = {"repeats": config.repeat, "steps": config.repeat * len(config.scenarios),
                         "seed": config.seed, "shuffle": config.shuffle, "config": config.model_dump(),
                         "repeat": 1, "step": 0}  # fmt: skip
            self._phase("warmup", config.warmup_seconds)
            self.auto_task = asyncio.create_task(self.run_auto(config))
            return self.status()

    def _phase(self, phase, seconds, *, repeat=None, step=None, scenario=None):
        started = time.time()
        self.auto.update(
            phase=phase,
            repeat=self.auto["repeat"] if repeat is None else repeat,
            step=self.auto["step"] if step is None else step,
            scenario=scenario,
            phase_started_at=started,
            phase_ends_at=None if seconds is None else started + seconds,
        )
        self.log.record(
            "phase_started",
            phase=phase,
            repeat=self.auto["repeat"],
            step=self.auto["step"],
            scenario=scenario,
        )

    async def run_auto(self, config):
        order = config.scenarios.copy()
        outcome = "cycle_stopped"  # unless it finishes or fails below
        try:
            await asyncio.sleep(config.warmup_seconds)
            rng = random.Random(config.seed)
            step = 0
            for repeat in range(1, config.repeat + 1):
                if config.shuffle:
                    rng.shuffle(order)
                for scenario in order:
                    step += 1
                    self._phase("fault", config.duration_seconds, repeat=repeat, step=step, scenario=scenario)
                    await self.activate(
                        Activation(scenario=scenario, duration_seconds=config.duration_seconds),
                        automatic=True,
                    )
                    await asyncio.sleep(config.duration_seconds)
                    await self.deactivate(scenario, automatic=True)
                    self._phase("recovery", config.recovery_seconds, scenario=scenario)
                    await asyncio.sleep(config.recovery_seconds)
            outcome = "cycle_finished"
        except AppError:
            outcome = "cycle_failed"
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
                self.auto = None
                self.log.record(outcome)

    async def stop_auto(self):
        async with self.stop_lock:
            async with self.lock:
                self.stopping = True
                task = self.auto_task
                if task and not task.done() and self.auto:
                    self.log.record("cycle_stop_requested")
                    self.auto.update(phase="stopping", phase_started_at=time.time(), phase_ends_at=None)
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
        interrupted = 0
        for record in self.journal.records:
            if record["ended_at"] is None:
                record.update(
                    ended_at=utcnow(), status="ended", end_reason="controller_restart", interrupted=True
                )
                interrupted += 1
        self.journal.write()
        self.log.record("controller_started", interrupted_incidents=interrupted)
        self.expiry_task = asyncio.create_task(self.expire())
        if self.health:
            self.health_task = asyncio.create_task(self.watch_health())
        operational("controller_started")

    async def close(self):
        try:
            await self.stop_auto()
        except AppError:
            pass
        for task in (self.expiry_task, self.health_task):
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        await self.client.aclose()
        operational("controller_stopped")


def create_app(*, path=None, client=None, health=None, health_interval=None):
    controller = Controller(
        path or os.getenv("GROUND_TRUTH_PATH", "/ground-truth/incidents.jsonl"),
        client,
        health=health if health is not None else default_health(),
        health_interval=health_interval
        if health_interval is not None
        else float(os.getenv("HEALTH_INTERVAL_SECONDS", "5")),
    )

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

    @app.get("/services/health", response_model=HealthSnapshot)
    async def services_health():
        """Latest probe + Prometheus check of each business service; never derived from fault state."""
        return controller.health_snapshot()

    @app.get("/activity", response_model=Activity)
    async def activity(limit: Annotated[int | None, Query(ge=1, le=1000)] = None):
        """This controller session's control actions and state changes, newest first."""
        return controller.activity(limit)

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
