"""Operator-facing controller state: projection, transitions, controls and the activity log."""

import asyncio
import json
import time

import httpx
import pytest

from controllers.fault_injector.app import Activation, AutoConfig, Controller, create_app
from services.shared.errors import AppError

GROUND_TRUTH_ONLY = ("root_cause", "root_cause_service", "expected_signals")


class Targets:
    """Fake Orders/Payments control endpoints that can stop answering."""

    def __init__(self):
        self.up, self.calls = True, []

    def __call__(self, request):
        self.calls.append(request)
        if not self.up:
            raise httpx.ConnectError("target unreachable")
        return httpx.Response(200, json={"status": "ok"})


@pytest.fixture
async def controller(tmp_path):
    targets = Targets()
    instance = Controller(
        tmp_path / "incidents.jsonl", httpx.AsyncClient(transport=httpx.MockTransport(targets))
    )
    await instance.open()
    yield instance, targets
    await instance.close()


def leaked(value):
    text = json.dumps(value)
    return [key for key in GROUND_TRUTH_ONLY if f'"{key}"' in text]


async def test_operator_status_never_carries_ground_truth_but_the_journal_does(controller):
    ctl, _ = controller
    await ctl.activate(Activation(scenario="payments_errors", duration_seconds=5))
    assert not leaked(ctl.status())
    await ctl.deactivate("payments_errors")
    assert not leaked(ctl.status())
    await ctl.start_auto(AutoConfig(warmup_seconds=0, duration_seconds=5, recovery_seconds=0))
    await asyncio.sleep(0.05)
    assert ctl.status()["active"] and not leaked(ctl.status())
    await ctl.stop_auto()
    journal = [json.loads(line) for line in ctl.journal.path.read_text().splitlines()]
    assert all(set(GROUND_TRUTH_ONLY) <= record.keys() for record in journal)


def enabled(status):
    return {name for name, control in status["controls"].items() if control["enabled"]}


async def test_a_manual_fault_reports_its_deadline_and_which_controls_are_safe(controller):
    ctl, _ = controller
    idle = ctl.status()
    assert (idle["state"], enabled(idle)) == ("idle", {"activate", "auto_start"})

    before = time.time()
    active = await ctl.activate(Activation(scenario="bad_deployment", duration_seconds=5))

    assert active["state"] == "manual" and active["active"]["status"] == "active"
    assert before + 5 <= active["active"]["expires_at"] <= time.time() + 5
    assert abs(active["now"] - time.time()) < 1  # the client counts down against the server clock
    assert enabled(active) == {"deactivate"}
    assert active["controls"]["activate"]["reason"] == "fault_active"
    ended = await ctl.deactivate("bad_deployment")
    assert ended["state"] == "idle" and ended["last_fault"]["end_reason"] == "manual"
    assert ended["last_fault"]["scenario"] == "bad_deployment" and ended["last_fault"]["ended_at"]


async def test_an_unconfirmed_stop_is_reported_as_unknown_until_a_retry_succeeds(controller):
    ctl, targets = controller
    await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
    targets.up = False

    with pytest.raises(AppError) as refused:
        await ctl.deactivate("payments_errors")

    assert refused.value.code == "target_unavailable"
    unknown = ctl.status()
    assert unknown["state"] == "unknown" and unknown["active"]["status"] == "deactivation_unconfirmed"
    assert enabled(unknown) == {"deactivate"}  # the only safe action is to retry the stop
    assert unknown["controls"]["activate"]["reason"] == "status_unknown"
    targets.up = True
    assert (await ctl.deactivate("payments_errors"))["state"] == "idle"


async def test_an_unconfirmed_activation_is_unknown_and_still_expires(controller):
    ctl, targets = controller
    targets.up = False
    with pytest.raises(AppError):
        await ctl.activate(Activation(scenario="payments_errors", duration_seconds=0.2))
    assert ctl.status()["state"] == "unknown"
    targets.up = True
    await asyncio.sleep(0.35)  # the target's own lease ends it even without a confirmation
    assert ctl.status()["state"] == "idle"
    assert ctl.status()["last_fault"]["end_reason"] in ("ttl", "target_lease_expired")


async def phases_of(ctl, config, seconds):
    """Sample the cycle until it ends, keeping each distinct (phase, repeat, step, scenario)."""
    await ctl.start_auto(config)
    seen, deadline = [], time.monotonic() + seconds
    while time.monotonic() < deadline and ctl.status()["auto_running"]:
        auto = ctl.status()["auto"]
        if auto:
            key = (auto["phase"], auto["repeat"], auto["step"], auto["scenario"])
            if not seen or seen[-1][0] != key:
                seen.append((key, auto))
        await asyncio.sleep(0.01)
    return seen


async def test_the_automatic_cycle_reports_phase_repeat_step_and_phase_deadline(controller):
    ctl, _ = controller
    config = AutoConfig(
        scenarios=["payments_errors", "bad_deployment"], warmup_seconds=0.15, duration_seconds=0.15,
        recovery_seconds=0.1, repeat=2, seed=7341, shuffle=False,
    )  # fmt: skip
    seen = await phases_of(ctl, config, 5)

    assert [key for key, _ in seen] == [
        ("warmup", 1, 0, None),
        ("fault", 1, 1, "payments_errors"), ("recovery", 1, 1, "payments_errors"),
        ("fault", 1, 2, "bad_deployment"), ("recovery", 1, 2, "bad_deployment"),
        ("fault", 2, 3, "payments_errors"), ("recovery", 2, 3, "payments_errors"),
        ("fault", 2, 4, "bad_deployment"), ("recovery", 2, 4, "bad_deployment"),
    ]  # fmt: skip
    _, fault = seen[1]
    assert (fault["repeats"], fault["steps"], fault["seed"], fault["shuffle"]) == (2, 4, 7341, False)
    assert fault["phase_ends_at"] - fault["phase_started_at"] == pytest.approx(0.15, abs=0.01)
    assert fault["config"]["recovery_seconds"] == 0.1
    assert ctl.status()["auto"] is None and ctl.status()["state"] == "idle"


class ScriptedHealth:
    """Fake HealthMonitor: returns the scripted service states in turn, then repeats the last."""

    def __init__(self, *rounds):
        self.rounds, self.checks = list(rounds), 0

    async def check(self):
        state = self.rounds[min(self.checks, len(self.rounds) - 1)]
        self.checks += 1
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time() * 1000) % 1000:03d}Z"
        probe = {"ok": True, "status_code": 200, "latency_ms": 1.0}
        rows = [{"service": "payments-service", "state": state, "checked_at": stamp, "probe": probe,
                 "error_ratio": 0.0, "mean_latency_ms": 10.0, "reasons": []}]  # fmt: skip
        return {"checked_at": stamp, "metrics": "available", "services": rows}


async def make(tmp_path, health, **kwargs):
    ctl = Controller(
        tmp_path / "incidents.jsonl",
        httpx.AsyncClient(transport=httpx.MockTransport(Targets())),
        health=health,
        health_interval=0.02,
        **kwargs,
    )
    await ctl.open()
    return ctl


async def test_after_a_fault_the_controller_is_recovering_until_health_checks_pass(tmp_path):
    health = ScriptedHealth("healthy")
    ctl = await make(tmp_path, health)
    try:
        await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
        health.rounds = ["degraded", "degraded", "degraded", "healthy"]
        health.checks = 0
        stopped = await ctl.deactivate("payments_errors")
        assert stopped["state"] == "recovering"  # no health check since the fault ended yet
        assert stopped["controls"]["activate"] == {"enabled": False, "reason": "recovering"}
        states = []
        for _ in range(50):
            states.append(ctl.status()["state"])
            if states[-1] == "idle":
                break
            await asyncio.sleep(0.02)
        assert "recovering" in states and states[-1] == "idle"
        assert ctl.health_snapshot()["services"][0]["state"] == "healthy"
    finally:
        await ctl.close()


async def test_recovery_gives_up_waiting_after_its_time_limit(tmp_path):
    ctl = await make(tmp_path, ScriptedHealth("unknown"), recovery_limit=0.1)
    try:
        await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
        assert (await ctl.deactivate("payments_errors"))["state"] == "recovering"
        await asyncio.sleep(0.15)
        assert ctl.status()["state"] == "idle"  # unknown health must not lock the controls forever
    finally:
        await ctl.close()


async def test_before_any_health_check_no_service_is_reported(tmp_path):
    ctl = Controller(
        tmp_path / "incidents.jsonl", httpx.AsyncClient(transport=httpx.MockTransport(Targets()))
    )
    snapshot = ctl.health_snapshot()
    assert snapshot["checked_at"] is None and snapshot["services"] == []


def events(ctl):
    return [entry["event"] for entry in ctl.activity()["entries"]]


async def test_the_activity_log_records_control_actions_and_outcomes_newest_first(controller):
    ctl, targets = controller
    await ctl.activate(
        Activation(scenario="db_connection_exhaustion", duration_seconds=30, parameters={"hold_seconds": 5})
    )
    with pytest.raises(AppError):
        await ctl.activate(Activation(scenario="bad_deployment"))
    targets.up = False
    with pytest.raises(AppError):
        await ctl.deactivate("db_connection_exhaustion")
    targets.up = True
    await ctl.deactivate("db_connection_exhaustion")

    assert events(ctl) == [
        "fault_stopped", "stop_requested", "stop_unconfirmed", "stop_requested",
        "control_rejected", "fault_active", "fault_requested", "controller_started",
    ]  # fmt: skip
    entries = ctl.activity()["entries"]
    requested = entries[-2]
    assert requested["scenario"] == "db_connection_exhaustion" and requested["parameters"] == {
        "hold_seconds": 5
    }
    assert requested["duration_seconds"] == 30 and requested["automatic"] is False and requested["at"]
    assert entries[4] | {"at": None} == {
        "at": None,
        "event": "control_rejected",
        "action": "activate",
        "error_code": "fault_conflict",
    }
    assert entries[0]["reason"] == "manual"
    assert not leaked(entries) and "connection-pool exhaustion" not in json.dumps(entries)


async def test_the_activity_log_follows_the_automatic_cycle(controller):
    ctl, _ = controller
    config = AutoConfig(
        scenarios=["payments_errors"],
        warmup_seconds=0.05,
        duration_seconds=0.1,
        recovery_seconds=0.05,
        seed=3,
    )
    await ctl.start_auto(config)
    await asyncio.wait_for(ctl.auto_task, 2)
    entries = list(reversed(ctl.activity()["entries"]))
    phases = [(e["phase"], e["repeat"], e["step"]) for e in entries if e["event"] == "phase_started"]
    assert phases == [("warmup", 1, 0), ("fault", 1, 1), ("recovery", 1, 1)]
    started = next(e for e in entries if e["event"] == "cycle_started")
    assert (started["repeats"], started["seed"], started["shuffle"], started["scenarios"]) == (
        1,
        3,
        False,
        ["payments_errors"],
    )
    assert entries[-1]["event"] == "cycle_finished"
    assert all(e["automatic"] for e in entries if e["event"] == "fault_requested")


async def test_the_activity_log_keeps_only_the_most_recent_entries(tmp_path):
    ctl = Controller(
        tmp_path / "incidents.jsonl",
        httpx.AsyncClient(transport=httpx.MockTransport(Targets())),
        activity_capacity=5,
    )
    await ctl.open()
    try:
        for _ in range(4):
            await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
            await ctl.deactivate("payments_errors")
        log = ctl.activity()
        assert log["capacity"] == 5 and len(log["entries"]) == 5
        assert log["entries"][0]["event"] == "fault_stopped"
        assert len(ctl.activity(limit=2)["entries"]) == 2
    finally:
        await ctl.close()


async def outcomes(*calls):
    results = await asyncio.gather(*calls, return_exceptions=True)
    return ["ok" if isinstance(r, dict) else r.code for r in results]


async def test_simultaneous_activations_leave_exactly_one_fault_running(controller):
    ctl, targets = controller
    scenarios = ["payments_errors", "bad_deployment", "db_connection_exhaustion"] * 3
    results = await outcomes(*(ctl.activate(Activation(scenario=s, duration_seconds=30)) for s in scenarios))
    assert results.count("ok") == 1 and set(results) == {"ok", "fault_conflict"}
    puts = [call for call in targets.calls if call.method == "PUT"]
    assert len(puts) == 1  # no rejected activation reached a target
    assert ctl.status()["state"] == "manual"
    assert [e["event"] for e in ctl.activity()["entries"]].count("control_rejected") == 8


async def test_a_manual_activation_and_a_cycle_start_never_both_win(controller):
    ctl, _ = controller
    cycle = AutoConfig(warmup_seconds=5, duration_seconds=5, recovery_seconds=0)
    for _ in range(5):
        results = await outcomes(
            ctl.activate(Activation(scenario="payments_errors", duration_seconds=30)), ctl.start_auto(cycle)
        )
        assert results.count("ok") == 1, results
        state = ctl.status()
        assert state["state"] in ("manual", "automatic")
        assert not (state["active"] and state["auto_running"] and state["auto"]["phase"] == "warmup")
        await ctl.stop_auto()
        if ctl.status()["active"]:
            await ctl.deactivate(ctl.status()["active"]["scenario"])
        assert ctl.status()["state"] == "idle"


async def test_overlapping_stop_requests_end_the_cycle_cleanly(controller):
    ctl, _ = controller
    await ctl.start_auto(AutoConfig(warmup_seconds=0, duration_seconds=30, recovery_seconds=0))
    await asyncio.sleep(0.05)
    assert ctl.status()["auto"]["phase"] == "fault"
    stopping = asyncio.create_task(ctl.stop_auto())
    await asyncio.sleep(0)
    during = ctl.status()
    results = await outcomes(stopping, ctl.stop_auto(), ctl.stop_auto())
    assert results == ["ok", "ok", "ok"]
    assert during["auto_running"] and during["auto"]["phase"] == "stopping"
    assert during["controls"]["activate"]["enabled"] is False
    final = ctl.status()
    assert (final["state"], final["auto"], final["active"]) == ("idle", None, None)
    assert final["last_fault"]["end_reason"] == "auto_stopped"
    events = [e["event"] for e in ctl.activity()["entries"]]
    assert events.count("cycle_stop_requested") == 1 and events[0] == "cycle_stopped"


async def test_the_operator_api_serves_status_health_and_activity_without_ground_truth(tmp_path):
    health = ScriptedHealth("healthy")
    app = create_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(Targets())),
        health=health,
        health_interval=0.02,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8090") as client,
    ):
        activated = await client.post(
            "/faults/activate", json={"scenario": "payments_errors", "parameters": {"failure_rate": 1}}
        )
        assert activated.status_code == 200
        status = (await client.get("/faults/status")).json()
        assert status["state"] == "manual" and status["auto_running"] is False  # existing keys stay
        assert status["active"]["parameters"] == {"failure_rate": 1}  # as requested, not runtime flags
        await asyncio.sleep(0.05)
        snapshot = (await client.get("/services/health")).json()
        assert snapshot["services"][0] == {
            **snapshot["services"][0],
            "service": "payments-service",
            "state": "healthy",
        }
        assert (await client.post("/faults/activate", json={"scenario": "bad_deployment"})).status_code == 409
        activity = (await client.get("/activity")).json()
        controls = [e["event"] for e in activity["entries"] if e["event"] != "service_health_changed"]
        assert controls[:3] == ["control_rejected", "fault_active", "fault_requested"]
        assert len((await client.get("/activity", params={"limit": 2})).json()["entries"]) == 2
        assert activity["capacity"] == 200
        assert (await client.get("/activity", params={"limit": 0})).status_code == 422
        bodies = [activated.json(), status, snapshot, (await client.get("/activity")).json()]
        assert not leaked(bodies)


async def test_operators_see_the_parameters_they_chose_not_the_runtime_release_flags(controller):
    ctl, _ = controller
    await ctl.activate(
        Activation(scenario="bad_deployment", duration_seconds=30, parameters={"delay_seconds": 1.5})
    )
    status = ctl.status()
    assert status["active"]["parameters"] == {"delay_seconds": 1.5}
    text = json.dumps([status, ctl.activity()])
    assert "v2-buggy" not in text and "release_version" not in text
    assert ctl.journal.records[-1]["parameters"]["release_version"] == "v2-buggy"  # ground truth keeps it


async def test_a_started_cycle_reports_its_warmup_at_once(controller):
    ctl, _ = controller
    started = await ctl.start_auto(AutoConfig(warmup_seconds=5, duration_seconds=5, recovery_seconds=0))
    assert started["auto"]["phase"] == "warmup" and started["auto"]["phase_ends_at"] > started["now"]
    await ctl.stop_auto()


async def test_health_changes_and_recovery_are_logged(tmp_path):
    health = ScriptedHealth("healthy")
    ctl = await make(tmp_path, health)
    try:
        await asyncio.sleep(0.05)
        await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
        health.rounds, health.checks = ["degraded", "degraded", "healthy"], 0
        await ctl.deactivate("payments_errors")
        for _ in range(50):
            if ctl.status()["state"] == "idle":
                break
            await asyncio.sleep(0.02)
        entries = list(reversed(ctl.activity()["entries"]))
        changes = [
            (e["service"], e["previous"], e["state"])
            for e in entries
            if e["event"] == "service_health_changed"
        ]
        assert changes == [
            ("payments-service", None, "healthy"),
            ("payments-service", "healthy", "degraded"),
            ("payments-service", "degraded", "healthy"),
        ]
        assert [e["event"] for e in entries].count("services_recovered") == 1
        recovered = next(i for i, e in enumerate(entries) if e["event"] == "services_recovered")
        stopped = next(i for i, e in enumerate(entries) if e["event"] == "fault_stopped")
        assert recovered > stopped
    finally:
        await ctl.close()


async def test_a_restart_reports_only_the_incidents_it_interrupted(tmp_path):
    journal = tmp_path / "incidents.jsonl"
    old = {"incident_id": "inc-old", "ended_at": "2026-01-01T00:00:00Z", "end_reason": "controller_restart"}
    open_one = {"incident_id": "inc-open", "ended_at": None, "status": "active"}
    journal.write_text(json.dumps(old) + "\n" + json.dumps(open_one) + "\n")
    ctl = Controller(journal, httpx.AsyncClient(transport=httpx.MockTransport(Targets())))
    await ctl.open()
    try:
        started = ctl.activity()["entries"][-1]
        assert started["event"] == "controller_started" and started["interrupted_incidents"] == 1
    finally:
        await ctl.close()


async def refused(call):
    with pytest.raises(AppError) as error:
        await call
    return error.value.status, error.value.code


async def test_while_recovering_the_server_refuses_new_faults_but_still_allows_stops(tmp_path):
    health = ScriptedHealth("degraded")
    ctl = await make(tmp_path, health, recovery_limit=30)
    try:
        await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
        assert (await ctl.deactivate("payments_errors"))["state"] == "recovering"

        assert await refused(ctl.activate(Activation(scenario="bad_deployment"))) == (409, "recovering")
        assert await refused(ctl.start_auto(AutoConfig(warmup_seconds=0))) == (409, "recovering")
        assert (await ctl.deactivate("payments_errors"))["state"] == "recovering"  # a stop is always allowed
        assert (await ctl.stop_auto())["state"] == "recovering"
        rejected = [e for e in ctl.activity()["entries"] if e["event"] == "control_rejected"]
        assert [(e["action"], e["error_code"]) for e in rejected] == [
            ("auto_start", "recovering"),
            ("activate", "recovering"),
        ]

        health.rounds, health.checks = ["healthy"], 0
        for _ in range(50):
            if ctl.status()["state"] == "idle":
                break
            await asyncio.sleep(0.02)
        assert (await ctl.activate(Activation(scenario="bad_deployment", duration_seconds=30)))[
            "state"
        ] == "manual"
    finally:
        await ctl.close()


async def test_recovery_stops_refusing_once_its_time_limit_passes(tmp_path):
    ctl = await make(tmp_path, ScriptedHealth("unknown"), recovery_limit=0.1)
    try:
        await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
        await ctl.deactivate("payments_errors")
        assert await refused(ctl.activate(Activation(scenario="payments_errors"))) == (409, "recovering")
        await asyncio.sleep(0.15)
        assert (await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30)))[
            "state"
        ] == "manual"
    finally:
        await ctl.close()


async def test_with_the_fault_status_unknown_only_stopping_is_allowed(controller):
    ctl, targets = controller
    await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
    targets.up = False
    await refused(ctl.deactivate("payments_errors"))
    assert ctl.status()["state"] == "unknown"

    assert await refused(ctl.activate(Activation(scenario="bad_deployment"))) == (409, "status_unknown")
    assert await refused(ctl.start_auto(AutoConfig(warmup_seconds=0))) == (409, "status_unknown")
    assert await refused(ctl.deactivate("payments_errors")) == (503, "target_unavailable")  # retry allowed
    # stop_auto also stops a manual fault (existing behaviour): it is attempted, still unconfirmed
    assert await refused(ctl.stop_auto()) == (503, "target_unavailable")
    assert ctl.status()["state"] == "unknown"
    targets.up = True
    assert (await ctl.deactivate("payments_errors"))["state"] == "idle"


async def test_the_automatic_cycle_is_not_held_back_by_health_between_its_own_faults(tmp_path):
    ctl = await make(tmp_path, ScriptedHealth("degraded"), recovery_limit=30)
    try:
        config = AutoConfig(
            scenarios=["payments_errors", "bad_deployment"],
            warmup_seconds=0,
            duration_seconds=0.1,
            recovery_seconds=0.05,
        )
        await ctl.start_auto(config)
        await asyncio.wait_for(ctl.auto_task, 3)
        assert [r["scenario"] for r in ctl.journal.records] == ["payments_errors", "bad_deployment"]
        assert "cycle_finished" in [e["event"] for e in ctl.activity()["entries"]]
    finally:
        await ctl.close()


async def test_an_unconfirmed_stop_never_reveals_the_fault_cause(tmp_path):
    targets = Targets()
    app = create_app(
        path=tmp_path / "incidents.jsonl", client=httpx.AsyncClient(transport=httpx.MockTransport(targets))
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1:8090") as client,
    ):
        activated = await client.post(
            "/faults/activate", json={"scenario": "bad_deployment", "parameters": {"delay_seconds": 1.5}}
        )
        targets.up = False
        stop = await client.post("/faults/deactivate", json={"scenario": "bad_deployment"})
        retry_block = await client.post("/faults/activate", json={"scenario": "payments_errors"})
        status = await client.get("/faults/status")
        activity = await client.get("/activity")
        journal = [json.loads(line) for line in (tmp_path / "incidents.jsonl").read_text().splitlines()]
        targets.up = True

    assert (stop.status_code, stop.json()["error_code"]) == (503, "target_unavailable")
    assert retry_block.json()["error_code"] == "status_unknown"
    assert status.json()["active"]["status"] == "deactivation_unconfirmed"
    text = json.dumps([activated.json(), stop.json(), retry_block.json(), status.json(), activity.json()])
    for secret in ("root_cause", "expected_signals", "OrderPersistenceCompatibilityError", "v2-buggy",
                   "release_version", "persistence_delay_seconds", "orders-service"):  # fmt: skip
        assert secret not in text, secret
    record = journal[-1]  # read while the stop was still unconfirmed
    assert record["status"] == "deactivation_unconfirmed"
    assert record["root_cause_service"] == "orders-service" and record["expected_signals"]


async def test_concurrent_controls_while_unknown_refuse_every_new_fault_and_let_one_stop_through(controller):
    ctl, targets = controller
    await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
    targets.up = False
    await refused(ctl.deactivate("payments_errors"))
    targets.up = True
    results = await outcomes(
        ctl.activate(Activation(scenario="bad_deployment")),
        ctl.deactivate("payments_errors"),
        ctl.start_auto(AutoConfig(warmup_seconds=0)),
        ctl.activate(Activation(scenario="db_connection_exhaustion")),
    )
    # The lock serves them in arrival order: refused while unknown, the stop confirms, and only then
    # (no health monitor here, so no recovery wait) a new cycle may start, which locks out the rest.
    assert results == ["status_unknown", "ok", "ok", "auto_running"]
    assert sum(1 for r in ctl.journal.records if r["ended_at"] is None) <= 1
    await ctl.stop_auto()


async def test_concurrent_new_faults_while_recovering_are_all_refused(tmp_path):
    ctl = await make(tmp_path, ScriptedHealth("degraded"), recovery_limit=30)
    try:
        await ctl.activate(Activation(scenario="payments_errors", duration_seconds=30))
        await ctl.deactivate("payments_errors")
        results = await outcomes(
            *(ctl.activate(Activation(scenario=s)) for s in ("payments_errors", "bad_deployment") * 3),
            ctl.start_auto(AutoConfig(warmup_seconds=0)),
            ctl.stop_auto(),
            ctl.deactivate("payments_errors"),
        )
        assert results[:7] == ["recovering"] * 7 and results[7:] == ["ok", "ok"]
        assert ctl.status()["state"] == "recovering" and len(ctl.journal.records) == 1
    finally:
        await ctl.close()
