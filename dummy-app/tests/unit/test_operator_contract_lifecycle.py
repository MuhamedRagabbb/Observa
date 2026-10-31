"""P5-F: what the contract harness does around the controller: its links, its docs routes, where it keeps
its journal, and how it starts and stops. Uvicorn is replaced by fakes, so nothing here opens a socket,
starts the controller or reaches any service."""

import asyncio
import json
import signal
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tests.harness import operator_contract as harness

ROOT = Path(__file__).resolve().parents[2]
PORTS = json.loads((ROOT / "web" / "contracts" / "stack-ports.json").read_text("utf-8"))
GOOD = {"CONTRACT_CONTROLLER_PORT": "18190", "CONTRACT_CONTROL_PORT": "18191"}
SIGNALS = [signal.SIGINT, signal.SIGTERM, *([signal.SIGBREAK] if sys.platform == "win32" else [])]


@pytest.fixture
def dist(tmp_path):
    (tmp_path / "dist" / "operator").mkdir(parents=True)
    (tmp_path / "dist" / "operator" / "index.html").write_text("<!doctype html>", "utf-8")
    return tmp_path / "dist"


# --- no proxy can carry a request somewhere else -------------------------------------------------


@pytest.mark.parametrize("name", ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"])
def test_an_inherited_proxy_is_refused(dist, name):
    with pytest.raises(harness.UnsafeHarness, match=name):
        harness.validate_isolation({**GOOD, name: "http://127.0.0.1:8090"}, web_dist=dist)


def test_no_proxy_alone_is_harmless(dist):
    assert harness.validate_isolation({**GOOD, "NO_PROXY": "127.0.0.1"}, web_dist=dist).controller_port == 18190


# --- the links the page publishes point only at the harness --------------------------------------


def test_every_published_link_is_the_harness_itself_never_a_live_or_preview_service(dist):
    config = harness.validate_isolation(GOOD, web_dist=dist)
    env = harness.controller_environment(config)
    public = {key: int(value) for key, value in env.items() if key.startswith("PUBLIC_")}
    assert public["PUBLIC_CONTROLLER_PORT"] == config.controller_port
    for key, port in public.items():
        assert port in (config.controller_port, config.control_port), key
        assert port not in PORTS["live"], key
        assert port not in PORTS["preview"].values(), key


# --- the real controller, without its documentation routes ---------------------------------------


@pytest.fixture
def controller_app(dist, tmp_path):
    config = harness.validate_isolation(GOOD, web_dist=dist)
    return harness.build_controller_app(config, tmp_path / "incidents.jsonl", harness.StubHealth())


def test_the_harness_controller_has_no_documentation_or_schema_routes(controller_app):
    paths = {getattr(route, "path", None) for route in controller_app.routes}
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert path not in paths, path
    assert {"/faults/status", "/faults/activate", "/health"} <= paths


async def test_those_routes_answer_404_and_the_guard_is_still_on(controller_app):
    transport = httpx.ASGITransport(app=controller_app)  # no lifespan: the controller is not started
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18190") as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await client.get(path)).status_code == 404, path
        foreign = await client.post("/faults/auto/stop", headers={"Origin": "http://evil.test"})
        assert foreign.status_code == 403
        assert foreign.json()["error_code"] == "origin_not_allowed"


# --- the ground-truth journal has one known home --------------------------------------------------


def test_the_journal_lives_in_the_harness_output_folder():
    assert harness.JOURNAL_ROOT == ROOT / "web" / "e2e-output"
    assert harness.JOURNAL_DIR == harness.JOURNAL_ROOT / "contract-journal"


def test_preparing_the_journal_clears_only_the_old_journal_file(tmp_path):
    root = tmp_path / "out"
    directory = root / "journal"
    directory.mkdir(parents=True)
    (directory / "incidents.jsonl").write_text("old", "utf-8")
    (directory / "other.txt").write_text("keep", "utf-8")
    path = harness.prepare_journal(directory, allowed_root=root)
    assert path == directory / "incidents.jsonl"
    assert not path.exists()
    assert (directory / "other.txt").read_text("utf-8") == "keep"


def test_preparing_the_journal_creates_a_missing_folder(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    path = harness.prepare_journal(root / "journal", allowed_root=root)
    assert path.parent.is_dir()


@pytest.mark.parametrize("where", ["elsewhere", "../escape", "out"])
def test_a_journal_folder_outside_the_output_folder_is_refused(tmp_path, where):
    root = tmp_path / "out"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    target = root / where if where.startswith("..") else tmp_path / where
    with pytest.raises(harness.UnsafeHarness):
        harness.prepare_journal(target, allowed_root=root)


def test_a_journal_path_that_is_a_file_or_a_link_is_refused(tmp_path):
    root = tmp_path / "out"
    root.mkdir()
    (root / "journal").write_text("not a folder", "utf-8")
    with pytest.raises(harness.UnsafeHarness):
        harness.prepare_journal(root / "journal", allowed_root=root)
    directory = root / "real"
    directory.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", "utf-8")
    try:
        (directory / "incidents.jsonl").symlink_to(secret)
    except OSError:
        pytest.skip("cannot create symlinks here")
    with pytest.raises(harness.UnsafeHarness):
        harness.prepare_journal(directory, allowed_root=root)
    assert secret.read_text("utf-8") == "secret"


def test_the_controllers_temporary_journal_file_is_prepared_like_the_journal(tmp_path):
    # Journal.write() opens incidents.tmp for writing, so a link or folder there must never survive.
    root = tmp_path / "out"
    directory = root / "journal"
    directory.mkdir(parents=True)
    (directory / "incidents.tmp").write_text("stale", "utf-8")
    harness.prepare_journal(directory, allowed_root=root)
    assert not (directory / "incidents.tmp").exists()


def test_a_folder_named_like_the_temporary_journal_is_refused(tmp_path):
    root = tmp_path / "out"
    directory = root / "journal"
    (directory / "incidents.tmp").mkdir(parents=True)
    with pytest.raises(harness.UnsafeHarness):
        harness.prepare_journal(directory, allowed_root=root)


def test_a_link_named_like_the_temporary_journal_is_refused_and_its_target_left_alone(tmp_path):
    root = tmp_path / "out"
    directory = root / "journal"
    directory.mkdir(parents=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("precious", "utf-8")
    try:
        (directory / "incidents.tmp").symlink_to(outside)
    except OSError:
        pytest.skip("cannot create symlinks here")
    with pytest.raises(harness.UnsafeHarness):
        harness.prepare_journal(directory, allowed_root=root)
    assert outside.read_text("utf-8") == "precious"


# --- a sentinel the isolation self-tests use to prove a forbidden request was never made ----------


async def test_the_sentinel_counts_the_requests_that_reach_it_and_reset_clears_it():
    state = harness.StubState()
    transport = httpx.ASGITransport(app=harness.build_stub_app(state))
    header = {"X-Contract-Harness": "1"}
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18191") as client:
        assert (await client.get("/__test__/sentinel", headers=header)).json() == {"hits": 0}
        assert (await client.get("/sentinel")).status_code == 200
        assert (await client.get("/sentinel/deeper/path")).status_code == 200
        assert (await client.get("/__test__/sentinel", headers=header)).json() == {"hits": 2}
        await client.post("/__test__/reset", headers=header)
        assert (await client.get("/__test__/sentinel", headers=header)).json() == {"hits": 0}


async def test_reading_the_sentinel_needs_the_harness_header():
    transport = httpx.ASGITransport(app=harness.build_stub_app(harness.StubState()))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18191") as client:
        assert (await client.get("/__test__/sentinel")).status_code == 403


# --- the stub stalls for real -------------------------------------------------------------------


async def test_a_stalled_target_answers_late_not_at_once():
    state = harness.StubState(stall=0.3)
    transport = httpx.ASGITransport(app=harness.build_stub_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18191") as client:
        await client.post(
            "/__test__/mode", json={"target": "orders", "mode": "stall"}, headers={"X-Contract-Harness": "1"}
        )
        started = time.perf_counter()
        response = await client.delete("/orders/_control/runtime")
        assert response.status_code == 200
        assert time.perf_counter() - started >= 0.25


# --- serve: bounded, loopback-only, proxy-free, clean ---------------------------------------------


class FakeConfig:
    instances = []

    def __init__(self, app, **kwargs):
        self.app, self.kwargs, self.port = app, kwargs, kwargs.get("port")
        FakeConfig.instances.append(self)


class FakeServer:
    instances = []
    mode = "run"
    # A gate that keeps ONE server's lifespan startup suspended (uvicorn: awaiting the app's startup).
    gate = None
    gate_port = None
    startup_outcome = "ok"
    suspended = None  # set once the gated server is waiting inside its startup

    def __init__(self, config):
        self.config = config
        self.started_reads = 0  # how often the supervisor's start() loop looked at `started`
        self.started = self.should_exit = self.force_exit = False
        self.shutdown_ran = False  # whether the application's lifespan shutdown actually ran
        FakeServer.instances.append(self)

    @property
    def started(self):
        self.started_reads += 1
        return self._started

    @started.setter
    def started(self, value):
        self._started = value

    async def serve(self):
        if FakeServer.gate is not None and self.config.port == FakeServer.gate_port:
            if FakeServer.suspended is not None:
                FakeServer.suspended.set()
            await FakeServer.gate.wait()
            if FakeServer.startup_outcome == "fails":
                # uvicorn: lifespan.startup.failed -> should_exit, serve() returns normally, never started.
                self.lifespan = SimpleNamespace(startup_failed=True, shutdown_failed=False)
                self.should_exit = True
                return
            self.started = True
            if self.should_exit:
                return  # uvicorn returns right after startup when told to exit: no lifespan shutdown
            while not (self.should_exit or self.force_exit):  # noqa: ASYNC110 (polls like uvicorn's own loop)
                await asyncio.sleep(0.01)
            self.shutdown_ran = True
            return
        if FakeServer.mode == "bind_error":
            raise SystemExit(1)  # what uvicorn does when its port is taken
        if FakeServer.mode == "stubborn":
            # Started, but ignores should_exit: only force_exit (or a cancel) ends it.
            self.started = True
            while not self.force_exit:  # noqa: ASYNC110 (polls like uvicorn's own loop)
                await asyncio.sleep(0.01)
            return
        if FakeServer.mode in ("run", "fail_on_exit", "lifespan_failed"):
            self.started = True
        while not (self.should_exit or self.force_exit):  # noqa: ASYNC110 (polls like uvicorn's own loop)
            await asyncio.sleep(0.01)
        if FakeServer.mode == "run":
            self.shutdown_ran = True
        if FakeServer.mode == "fail_on_exit":
            raise RuntimeError("shutdown exploded")  # the server task ends with an exception
        if FakeServer.mode == "lifespan_failed":
            # uvicorn reports a failed lifespan shutdown through a flag: serve() returns normally.
            self.lifespan = SimpleNamespace(shutdown_failed=True, error_occured=True)


@pytest.fixture
def fakes(monkeypatch, tmp_path, dist):
    import uvicorn

    FakeConfig.instances, FakeServer.instances, FakeServer.mode = [], [], "run"
    FakeServer.gate, FakeServer.gate_port, FakeServer.startup_outcome = None, None, "ok"
    FakeServer.suspended = None
    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    built = []

    def fake_build(config, journal, health, *, client=None):
        built.append({"config": config, "journal": journal, "health": health, "client": client})
        return "controller-app"

    monkeypatch.setattr(harness, "build_controller_app", fake_build)
    monkeypatch.setattr(harness, "apply_environment", lambda config: None)
    monkeypatch.setattr(harness, "STARTUP_TIMEOUT", 2)
    monkeypatch.setattr(harness, "SHUTDOWN_TIMEOUT", 2)
    monkeypatch.setattr(harness, "JOURNAL_ROOT", tmp_path)
    monkeypatch.setattr(harness, "JOURNAL_ANCHOR", tmp_path.resolve())
    monkeypatch.setattr(harness, "JOURNAL_DIR", tmp_path / "journal")
    return built


def run_config(dist, seconds=4):
    return harness.HarnessConfig(18190, 18191, dist, seconds)


async def test_both_servers_bind_loopback_only_the_stub_first(fakes, dist):
    assert await harness.serve(run_config(dist)) == 0
    assert [config.kwargs["host"] for config in FakeConfig.instances] == ["127.0.0.1", "127.0.0.1"]
    assert [config.port for config in FakeConfig.instances] == [18191, 18190]
    assert isinstance(FakeConfig.instances[1].app, harness.RecordingApp)  # the recorder wraps the controller
    assert FakeConfig.instances[1].app.app == "controller-app"
    assert FakeConfig.instances[1].app.control_port == 18191  # the only redirect target it may serve
    for config in FakeConfig.instances:
        assert config.kwargs["log_level"] == "warning"
        assert config.kwargs["access_log"] is False


async def test_the_controller_gets_a_proxy_free_client_the_stub_health_and_the_harness_journal(fakes, dist, tmp_path):
    await harness.serve(run_config(dist))
    [call] = fakes
    assert isinstance(call["client"], httpx.AsyncClient)
    assert call["client"].trust_env is False
    assert isinstance(call["health"], harness.StubHealth)
    assert call["journal"] == tmp_path / "journal" / "incidents.jsonl"


async def test_ready_is_announced_once_both_servers_are_up(fakes, dist, capsys):
    await harness.serve(run_config(dist))
    out = capsys.readouterr().out
    assert out.count("contract-harness ready controller=http://127.0.0.1:18190 control=http://127.0.0.1:18191") == 1


async def test_a_normal_stop_asks_both_servers_to_exit(fakes, dist):
    assert await harness.serve(run_config(dist)) == 0
    assert [server.should_exit for server in FakeServer.instances] == [True, True]


async def test_the_lifetime_limit_ends_the_run(fakes, dist):
    started = time.perf_counter()
    assert await harness.serve(run_config(dist, seconds=4)) == 0
    assert 3.5 <= time.perf_counter() - started < 12


async def test_a_server_that_never_starts_ends_the_run_with_3_and_stops_everything(fakes, dist, monkeypatch, capsys):
    FakeServer.mode = "never_start"
    monkeypatch.setattr(harness, "STARTUP_TIMEOUT", 0.2)
    assert await harness.serve(run_config(dist, seconds=30)) == 3
    assert FakeServer.instances and all(server.should_exit for server in FakeServer.instances)
    assert capsys.readouterr().err


async def test_a_port_that_is_taken_ends_the_run_with_3_not_a_traceback(fakes, dist, capsys):
    FakeServer.mode = "bind_error"
    assert await harness.serve(run_config(dist)) == 3
    assert "contract-harness" in capsys.readouterr().err


async def test_signal_handlers_are_put_back(fakes, dist):
    before = {signum: signal.getsignal(signum) for signum in SIGNALS}
    await harness.serve(run_config(dist))
    assert {signum: signal.getsignal(signum) for signum in SIGNALS} == before


async def test_an_unsafe_journal_folder_stops_the_run_before_any_server_starts(fakes, dist, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(harness, "JOURNAL_DIR", tmp_path.parent)
    assert await harness.serve(run_config(dist)) == 2
    assert FakeServer.instances == []
    assert fakes == []
    assert "contract-harness refused" in capsys.readouterr().err
