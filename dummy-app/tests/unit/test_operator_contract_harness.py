"""P5-F: the local contract harness (tests/harness/operator_contract.py) runs the REAL controller app
against stub fault targets, so a browser can test the Operator Dashboard end to end without Docker,
a live service or a real fault.

These tests start nothing: no socket, no controller, no subprocess but one bare import check. They
prove that the harness refuses to start unless it is isolated (fail closed, no implicit fallback to
any live or preview service), and that its stub targets behave as the browser tests rely on.
"""

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from controllers.fault_injector.health import HEALTH_SERVICES, HealthSnapshot
from tests.harness import operator_contract as harness

ROOT = Path(__file__).resolve().parents[2]
PORTS = json.loads((ROOT / "web" / "contracts" / "stack-ports.json").read_text("utf-8"))
GOOD = {"CONTRACT_CONTROLLER_PORT": "18190", "CONTRACT_CONTROL_PORT": "18191"}
TEST_HEADER = {"X-Contract-Harness": "1"}


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "operator").mkdir()
    (tmp_path / "operator" / "index.html").write_text("<!doctype html>", "utf-8")
    return tmp_path


def check(env, dist):
    return harness.validate_isolation(env, web_dist=dist)


# --- the shared port list stays true -------------------------------------------------------------


def test_the_port_list_matches_what_the_compose_files_publish():
    def host_ports(name):
        text = (ROOT / name).read_text("utf-8")
        return {int(port) for port in re.findall(r"127\.0\.0\.1:\$\{[A-Z_0-9]+:-(\d+)\}:\d+", text)}

    assert host_ports("docker-compose.yml") <= set(PORTS["live"])
    assert set(PORTS["live"]) >= {5433}  # .env publishes the live Postgres here
    assert host_ports("docker-compose.preview.yml") == set(PORTS["preview"].values())
    assert not set(PORTS["live"]) & set(PORTS["preview"].values())
    assert harness.LIVE_PORTS == frozenset(PORTS["live"])
    assert harness.PREVIEW_PORTS == frozenset(PORTS["preview"].values())


# --- validate_isolation: fail closed ------------------------------------------------------------


def test_a_clean_explicit_configuration_is_accepted(dist):
    config = check(GOOD, dist)
    assert (config.controller_port, config.control_port) == (18190, 18191)
    assert config.web_dist == dist
    assert 10 <= config.max_seconds <= 3600
    assert harness.HOST == "127.0.0.1"


@pytest.mark.parametrize("missing", sorted(GOOD))
def test_a_missing_port_is_refused_not_defaulted(dist, missing):
    env = {k: v for k, v in GOOD.items() if k != missing}
    with pytest.raises(harness.UnsafeHarness, match=missing):
        check(env, dist)


@pytest.mark.parametrize(
    "value",
    ["", " 18190", "18190 ", "+18190", "-18190", "018190", "1e4", "0x4a2e", "18_190", "18190x", "١٨١٩٠", "18190.0"],
)
def test_a_malformed_port_is_refused(dist, value):
    with pytest.raises(harness.UnsafeHarness, match="CONTRACT_CONTROLLER_PORT"):
        check({**GOOD, "CONTRACT_CONTROLLER_PORT": value}, dist)
    with pytest.raises(harness.UnsafeHarness, match="CONTRACT_CONTROL_PORT"):
        check({**GOOD, "CONTRACT_CONTROL_PORT": value}, dist)


@pytest.mark.parametrize("value", ["0", "80", "1023", "65536", "99999"])
def test_a_port_outside_1024_to_65535_is_refused(dist, value):
    with pytest.raises(harness.UnsafeHarness, match="1024"):
        check({**GOOD, "CONTRACT_CONTROLLER_PORT": value}, dist)


@pytest.mark.parametrize("port", PORTS["live"])
def test_every_live_port_is_refused_for_either_server(dist, port):
    for name in GOOD:
        with pytest.raises(harness.UnsafeHarness, match="live"):
            check({**GOOD, name: str(port)}, dist)


@pytest.mark.parametrize("port", list(PORTS["preview"].values()))
def test_every_preview_port_is_refused_for_either_server(dist, port):
    for name in GOOD:
        with pytest.raises(harness.UnsafeHarness, match="preview"):
            check({**GOOD, name: str(port)}, dist)


def test_the_two_servers_cannot_share_a_port(dist):
    with pytest.raises(harness.UnsafeHarness, match="different"):
        check({**GOOD, "CONTRACT_CONTROL_PORT": GOOD["CONTRACT_CONTROLLER_PORT"]}, dist)


@pytest.mark.parametrize(
    "name",
    [
        "PROMETHEUS_URL",  # would make the controller read live metrics
        "ORDERS_CONTROL_URL",  # would point the controller at a real service
        "PAYMENTS_CONTROL_URL",
        "GROUND_TRUTH_PATH",
        "UI_BUNDLE_DIR",
        "HEALTH_INTERVAL_SECONDS",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "PUBLIC_GATEWAY_PORT",
        "PUBLIC_CONTROLLER_PORT",
        "PUBLIC_GRAFANA_PORT",
        "PUBLIC_ANYTHING_ELSE",
    ],
)
def test_an_inherited_setting_that_could_reach_a_real_service_is_refused(dist, name):
    with pytest.raises(harness.UnsafeHarness, match=name):
        check({**GOOD, name: "x"}, dist)
    with pytest.raises(harness.UnsafeHarness, match=name):
        check({**GOOD, name: ""}, dist)  # present but empty still counts


def test_a_missing_build_is_refused_with_the_way_to_fix_it(tmp_path):
    with pytest.raises(harness.UnsafeHarness, match="npm run build"):
        check(GOOD, tmp_path)
    (tmp_path / "operator").mkdir()
    with pytest.raises(harness.UnsafeHarness, match="npm run build"):
        check(GOOD, tmp_path)


@pytest.mark.parametrize("value", ["", "abc", "9", "3601", "-5", "1800.5", " 60", "1e3"])
def test_the_lifetime_limit_is_bounded_and_strict(dist, value):
    with pytest.raises(harness.UnsafeHarness, match="CONTRACT_MAX_SECONDS"):
        check({**GOOD, "CONTRACT_MAX_SECONDS": value}, dist)


def test_the_lifetime_limit_can_be_set_within_its_bounds(dist):
    assert check({**GOOD, "CONTRACT_MAX_SECONDS": "120"}, dist).max_seconds == 120


# --- what the controller will be given ----------------------------------------------------------


def test_the_controller_environment_names_only_the_stub_the_preview_ports_and_the_build(dist):
    config = check(GOOD, dist)
    env = harness.controller_environment(config)
    assert env["ORDERS_CONTROL_URL"] == "http://127.0.0.1:18191/orders"
    assert env["PAYMENTS_CONTROL_URL"] == "http://127.0.0.1:18191/payments"
    assert env["UI_BUNDLE_DIR"] == str(dist)
    assert "PROMETHEUS_URL" not in env  # health comes from the stub, never from Prometheus
    assert not [key for key in env if key.startswith("OTEL_")]
    # The links in /ui/config point at the controller itself and at preview (never live) ports.
    assert env["PUBLIC_CONTROLLER_PORT"] == "18190"
    for key, value in env.items():
        if key.startswith("PUBLIC_"):
            assert int(value) not in PORTS["live"], key
    assert set(env) == {
        "ORDERS_CONTROL_URL",
        "PAYMENTS_CONTROL_URL",
        "UI_BUNDLE_DIR",
        "PUBLIC_GATEWAY_PORT",
        "PUBLIC_CONTROLLER_PORT",
        "PUBLIC_GRAFANA_PORT",
        "PUBLIC_JAEGER_PORT",
        "PUBLIC_PROMETHEUS_PORT",
    }


def test_every_url_in_the_controller_environment_is_this_harness_on_loopback(dist):
    env = harness.controller_environment(check(GOOD, dist))
    for key in ("ORDERS_CONTROL_URL", "PAYMENTS_CONTROL_URL"):
        url = httpx.URL(env[key])
        assert (url.scheme, url.host, url.port) == ("http", "127.0.0.1", 18191), key


# --- main: validation comes first, always -------------------------------------------------------


def test_unsafe_settings_stop_before_anything_is_built_or_started(dist, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("must not be reached with an unsafe configuration")

    monkeypatch.setattr(harness, "build_controller_app", boom)
    monkeypatch.setattr(harness, "serve", boom)
    monkeypatch.setattr(harness, "apply_environment", boom)
    assert harness.main([], env={**GOOD, "PROMETHEUS_URL": "http://localhost:9090"}, web_dist=dist) == 2
    assert harness.main([], env={}, web_dist=dist) == 2
    assert harness.main([], env={**GOOD, "CONTRACT_CONTROLLER_PORT": "8090"}, web_dist=dist) == 2


def test_the_command_line_cannot_override_the_checked_settings(dist, monkeypatch):
    monkeypatch.setattr(harness, "serve", lambda config: pytest.fail("not reached"))
    for argv in (["--port", "8090"], ["--host", "0.0.0.0"], ["--controller-port", "8090"]):
        with pytest.raises(SystemExit) as stop:
            harness.main(argv, env=GOOD, web_dist=dist)
        assert stop.value.code != 0


def test_a_valid_configuration_reaches_serve_exactly_once_after_validation(dist, monkeypatch):
    seen = []

    async def fake_serve(config):
        seen.append(config)
        return 0

    monkeypatch.setattr(harness, "serve", fake_serve)
    assert harness.main([], env=GOOD, web_dist=dist) == 0
    assert [c.controller_port for c in seen] == [18190]


def test_importing_the_harness_does_not_import_the_controller():
    # The controller (and the stack it could reach) is only imported once the settings are valid.
    code = "import sys, tests.harness.operator_contract; print('controllers.fault_injector.app' in sys.modules)"
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60, check=True
    )
    assert done.stdout.strip() == "False"


def test_the_harness_only_ever_binds_loopback():
    source = Path(harness.__file__).read_text("utf-8")
    assert harness.HOST == "127.0.0.1"
    assert "0.0.0.0" not in source
    assert "host=\"::\"" not in source


# --- the stub fault targets ---------------------------------------------------------------------


@pytest.fixture
def state():
    return harness.StubState(hold_limit=0.05, stall=0.05)


@pytest.fixture
async def stub(state):
    transport = httpx.ASGITransport(app=harness.build_stub_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18191") as client:
        yield client


async def set_mode(stub, target, mode):
    response = await stub.post("/__test__/mode", json={"target": target, "mode": mode}, headers=TEST_HEADER)
    assert response.status_code == 200, response.text


async def test_a_target_accepts_a_lease_and_clears_it_like_a_real_service(stub):
    put = await stub.put("/payments/_control/runtime", json={"mode": "payments_errors"})
    assert (put.status_code, put.json()) == (200, {"status": "accepted"})
    delete = await stub.delete("/orders/_control/runtime")
    assert (delete.status_code, delete.json()) == (200, {"status": "cleared"})


async def test_calls_are_recorded_for_the_test_to_read(stub):
    await stub.put("/payments/_control/runtime", json={"x": 1})
    await stub.delete("/orders/_control/runtime")
    calls = (await stub.get("/__test__/calls", headers=TEST_HEADER)).json()["calls"]
    assert [(c["target"], c["method"]) for c in calls] == [("payments", "PUT"), ("orders", "DELETE")]
    assert calls[0]["body"] == {"x": 1}


async def test_the_call_log_is_bounded(stub, state):
    for _ in range(harness.MAX_CALLS + 25):
        await stub.delete("/orders/_control/runtime")
    calls = (await stub.get("/__test__/calls", headers=TEST_HEADER)).json()["calls"]
    assert len(calls) == harness.MAX_CALLS


async def test_a_failing_target_answers_500_so_the_controller_reports_target_unavailable(stub):
    await set_mode(stub, "payments", "fail")
    assert (await stub.put("/payments/_control/runtime", json={})).status_code == 500
    assert (await stub.put("/orders/_control/runtime", json={})).status_code == 200  # per target


async def test_all_targets_can_be_switched_at_once(stub):
    await set_mode(stub, "all", "fail")
    for target in ("orders", "payments"):
        assert (await stub.delete(f"/{target}/_control/runtime")).status_code == 500


async def test_a_held_request_waits_for_release_and_is_then_accepted(stub, state):
    state.hold_limit = 5
    await set_mode(stub, "payments", "hold")
    pending = asyncio.create_task(stub.put("/payments/_control/runtime", json={}))
    await asyncio.sleep(0.05)
    assert not pending.done()
    assert (await stub.post("/__test__/release", headers=TEST_HEADER)).status_code == 200
    assert (await asyncio.wait_for(pending, 2)).status_code == 200


async def test_a_hold_is_bounded_so_a_forgotten_one_cannot_hang_the_controller(stub):
    await set_mode(stub, "payments", "hold")
    assert (await stub.put("/payments/_control/runtime", json={})).status_code == 200  # after hold_limit


async def test_a_stall_outlasts_the_controllers_own_timeout_but_is_bounded(stub, state):
    assert harness.STALL_SECONDS > 3  # the controller's client gives up after 3 s
    await set_mode(stub, "orders", "stall")
    assert (await stub.delete("/orders/_control/runtime")).status_code == 200  # state.stall is tiny here


async def test_unknown_targets_and_paths_are_404(stub):
    assert (await stub.put("/inventory/_control/runtime", json={})).status_code == 404
    assert (await stub.get("/orders/_control/runtime")).status_code in (404, 405)
    assert (await stub.get("/faults/status")).status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"target": "inventory", "mode": "ack"},
        {"target": "orders", "mode": "explode"},
        {"target": "orders"},
        {"mode": "ack"},
        {},
    ],
)
async def test_the_test_api_refuses_unknown_targets_and_modes(stub, body):
    response = await stub.post("/__test__/mode", json=body, headers=TEST_HEADER)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/__test__/mode"),
        ("POST", "/__test__/release"),
        ("POST", "/__test__/reset"),
        ("POST", "/__test__/health"),
        ("GET", "/__test__/calls"),
        ("GET", "/__test__/health"),
    ],
)
async def test_the_test_api_needs_the_harness_header_which_a_web_page_cannot_send(stub, method, path):
    response = await stub.request(method, path, json={})
    assert response.status_code == 403
    assert response.json()["error_code"] == "harness_header_required"


async def test_a_request_for_another_host_is_refused(state):
    transport = httpx.ASGITransport(app=harness.build_stub_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://example.test:18191") as client:
        assert (await client.put("/orders/_control/runtime", json={})).status_code == 403
        assert (await client.get("/__test__/calls", headers=TEST_HEADER)).status_code == 403


async def test_reset_restores_every_default(stub, state):
    await set_mode(stub, "all", "fail")
    await stub.put("/orders/_control/runtime", json={})
    await stub.post("/__test__/health", json={"states": {"payments-service": "degraded"}}, headers=TEST_HEADER)
    assert (await stub.post("/__test__/reset", headers=TEST_HEADER)).status_code == 200
    assert (await stub.put("/orders/_control/runtime", json={})).status_code == 200
    calls = (await stub.get("/__test__/calls", headers=TEST_HEADER)).json()["calls"]
    assert [(c["target"], c["method"]) for c in calls] == [("orders", "PUT")]  # the log was cleared too
    health = (await stub.get("/__test__/health", headers=TEST_HEADER)).json()
    assert {row["state"] for row in health["services"]} == {"healthy"}


# --- the stub health monitor --------------------------------------------------------------------


async def test_default_health_is_a_valid_snapshot_of_the_controllers_four_services():
    snapshot = await harness.StubHealth().check()
    HealthSnapshot.model_validate(snapshot)
    assert [row["service"] for row in snapshot["services"]] == list(HEALTH_SERVICES)
    assert {row["state"] for row in snapshot["services"]} == {"healthy"}
    assert snapshot["metrics"] == "available"


async def test_health_states_can_be_set_and_stay_valid():
    health = harness.StubHealth()
    health.set_states({"payments-service": "degraded", "orders-service": "unknown"})
    snapshot = await health.check()
    HealthSnapshot.model_validate(snapshot)
    states = {row["service"]: row["state"] for row in snapshot["services"]}
    assert states["payments-service"] == "degraded"
    assert states["orders-service"] == "unknown"
    assert states["api-gateway"] == "healthy"


@pytest.mark.parametrize(
    "states",
    [{"nonexistent-service": "healthy"}, {"payments-service": "on-fire"}, {"payments-service": None}],
)
def test_health_refuses_unknown_services_and_states(states):
    with pytest.raises(ValueError):  # noqa: PT011
        harness.StubHealth().set_states(states)


async def test_health_reports_a_fresh_check_time_each_time():
    health = harness.StubHealth()
    first = (await health.check())["checked_at"]
    second = (await health.check())["checked_at"]
    assert first is not None
    assert second >= first
