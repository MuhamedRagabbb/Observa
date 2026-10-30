# ruff: noqa: F811  (fixtures imported from the lifecycle tests are used by name, the usual pytest idiom)
"""P5-F Gate A review: a cooperative stop for the harness.

On Windows Playwright stops its web server with `taskkill /T /F`, so the harness's own signal handling and
cleanup never run. The contract run's global teardown therefore asks the harness to stop through a
header-protected test endpoint first, and the harness must SAY how it ended: "stopped cleanly" only when
every server left by itself, and a distinct forced result when one had to be forced. No sockets here."""

import asyncio

import httpx
import pytest

from tests.harness import operator_contract as harness

# The fakes and fixtures of the lifecycle tests (uvicorn replaced; nothing is started or bound).
from tests.unit.test_operator_contract_lifecycle import (  # noqa: F401  (fixtures are used by name)
    FakeConfig,
    FakeServer,
    dist,
    fakes,
    run_config,
)

TEST = {"X-Contract-Harness": "1"}


@pytest.fixture
def state():
    return harness.StubState()


@pytest.fixture
async def stub(state):
    transport = httpx.ASGITransport(app=harness.build_stub_app(state))
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18191") as client:
        yield client


# --- the endpoint ------------------------------------------------------------------------------------


async def test_the_shutdown_endpoint_needs_the_harness_header(stub, state):
    called = []
    state.shutdown_hook = lambda: called.append(1)
    assert (await stub.post("/__test__/shutdown")).status_code == 403
    assert called == []


async def test_the_shutdown_endpoint_asks_for_a_stop(stub, state):
    called = []
    state.shutdown_hook = lambda: called.append(1)
    response = await stub.post("/__test__/shutdown", headers=TEST)
    assert (response.status_code, response.json()) == (200, {"status": "stopping"})
    assert called == [1]


async def test_it_answers_even_without_a_hook_and_when_asked_twice(stub, state):
    assert (await stub.post("/__test__/shutdown", headers=TEST)).status_code == 200
    called = []
    state.shutdown_hook = lambda: called.append(1)
    await stub.post("/__test__/shutdown", headers=TEST)
    await stub.post("/__test__/shutdown", headers=TEST)
    assert called == [1, 1]  # the hook itself is idempotent (an event); each request may call it


async def test_the_shutdown_endpoint_is_post_only_and_a_nonce_does_not_open_it(stub):
    assert (await stub.get("/__test__/shutdown", headers=TEST)).status_code in (404, 405)
    assert (await stub.post("/__test__/shutdown", params={"nonce": "0" * 32})).status_code == 403


async def test_a_shutdown_request_is_not_a_sentinel_contact(stub, state):
    await stub.post("/__test__/shutdown", headers=TEST)
    assert state.sentinel_hits == 0


# --- serve ends cooperatively, and says how it ended ---------------------------------------------------


async def wait_until_started(seconds=3.0):
    for _ in range(int(seconds / 0.01)):
        if len(FakeServer.instances) == 2 and all(server.started for server in FakeServer.instances):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the fake servers never started")


async def request_shutdown():
    transport = httpx.ASGITransport(app=FakeConfig.instances[0].app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:18191") as client:
        return await client.post("/__test__/shutdown", headers=TEST)


async def test_a_shutdown_request_ends_the_run_cleanly_and_says_so(fakes, dist, capsys):
    task = asyncio.create_task(harness.serve(run_config(dist, seconds=60)))
    await wait_until_started()
    assert (await request_shutdown()).status_code == 200
    assert await asyncio.wait_for(task, 5) == 0
    out = capsys.readouterr().out
    assert "contract-harness stopped cleanly" in out
    assert [server.should_exit for server in FakeServer.instances] == [True, True]
    assert not any(server.force_exit for server in FakeServer.instances)


async def test_a_server_that_ignores_the_stop_is_forced_and_the_run_says_so(fakes, dist, monkeypatch, capsys):
    FakeServer.mode = "stubborn"
    monkeypatch.setattr(harness, "SHUTDOWN_TIMEOUT", 0.2)
    task = asyncio.create_task(harness.serve(run_config(dist, seconds=60)))
    await wait_until_started()
    await request_shutdown()
    assert await asyncio.wait_for(task, 5) == 4
    captured = capsys.readouterr()
    assert "stopped cleanly" not in captured.out + captured.err
    assert "contract-harness stopped by force" in captured.err
    assert all(server.force_exit for server in FakeServer.instances)


# --- a stop that arrives while the controller's lifespan startup is still suspended --------------------
# uvicorn returns right after startup when it was told to exit meanwhile, SKIPPING the lifespan shutdown;
# and a failed startup returns normally with startup_failed set. Neither may end "stopped cleanly".


async def wait_until(condition, what, seconds=10.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.005)


async def stop_while_the_controller_starts(dist, outcome, monkeypatch):
    # The startup budget must be far above everything this helper waits for, so a startup TIMEOUT can
    # never be what ends the run (the production value is 30 s; only the fixture lowers it).
    monkeypatch.setattr(harness, "STARTUP_TIMEOUT", 30)
    FakeServer.gate, FakeServer.gate_port, FakeServer.startup_outcome = asyncio.Event(), 18190, outcome
    FakeServer.suspended = asyncio.Event()
    task = asyncio.create_task(harness.serve(run_config(dist, seconds=60)))
    # Checkpoint 1: the controller's lifespan startup is suspended (the stub has started by then).
    await asyncio.wait_for(FakeServer.suspended.wait(), 10)
    stub, controller = FakeServer.instances
    assert stub.started and not controller.started
    # The stop is requested; the endpoint calls the supervisor's stop hook before it answers.
    assert (await request_shutdown()).status_code == 200
    # Checkpoint 2: the supervisor's start() loop has gone round at least twice since the stop, so the
    # previous implementation had certainly acted on it (it returned early on a set stop).
    after_stop = controller.started_reads
    await wait_until(lambda: controller.started_reads >= after_stop + 2, "the supervisor to poll again")
    assert not task.done()
    FakeServer.gate.set()
    return await asyncio.wait_for(task, 10)


async def test_a_stop_during_a_suspended_startup_still_runs_the_application_shutdown(
    fakes, dist, capsys, monkeypatch
):
    assert await stop_while_the_controller_starts(dist, "ok", monkeypatch) == 0
    captured = capsys.readouterr()
    assert "timed out" not in captured.err
    assert "contract-harness stopped cleanly" in captured.out
    started = [server for server in FakeServer.instances if server.started]
    assert len(started) == 2
    assert all(server.shutdown_ran for server in started)  # "clean" only if the shutdown really ran


async def test_a_stop_during_a_suspended_startup_that_then_fails_exits_3_not_clean(
    fakes, dist, capsys, monkeypatch
):
    assert await stop_while_the_controller_starts(dist, "fails", monkeypatch) == 3
    controller = FakeServer.instances[1]
    # The fake really reached uvicorn's failed-startup outcome (lifespan flag set, never started) ...
    assert controller.lifespan.startup_failed is True
    assert controller._started is False
    captured = capsys.readouterr()
    assert "stopped cleanly" not in captured.out + captured.err
    # ... and the harness reported exactly that, not a startup timeout.
    assert "contract-harness startup/server failed: server on 127.0.0.1:18190 did not start" in captured.err
    assert "timed out" not in captured.err


@pytest.mark.parametrize("mode", ["fail_on_exit", "lifespan_failed"])
async def test_a_failed_shutdown_never_claims_a_clean_stop_or_exits_zero(fakes, dist, capsys, mode):
    FakeServer.mode = mode
    task = asyncio.create_task(harness.serve(run_config(dist, seconds=60)))
    await wait_until_started()
    await request_shutdown()
    code = await asyncio.wait_for(task, 5)
    captured = capsys.readouterr()
    assert code not in (0, 3, 4)  # neither success, nor "failed to start", nor "forced"
    assert "stopped cleanly" not in captured.out + captured.err
    assert "contract-harness shutdown failed" in captured.err


async def test_a_failed_shutdown_after_the_lifetime_limit_is_not_clean_either(fakes, dist, capsys):
    FakeServer.mode = "lifespan_failed"
    assert await harness.serve(run_config(dist, seconds=4)) not in (0, 3, 4)
    assert "stopped cleanly" not in capsys.readouterr().out


async def test_the_lifetime_limit_also_ends_cleanly_and_says_so(fakes, dist, capsys):
    assert await harness.serve(run_config(dist, seconds=4)) == 0
    assert "contract-harness stopped cleanly" in capsys.readouterr().out


async def test_a_failed_start_does_not_claim_a_clean_stop(fakes, dist, monkeypatch, capsys):
    FakeServer.mode = "bind_error"
    assert await harness.serve(run_config(dist)) == 3
    captured = capsys.readouterr()
    assert "stopped cleanly" not in captured.out + captured.err


# --- the harness says which process it is, so a stop can be proven by that process being gone ------------


async def test_the_alive_endpoint_reports_the_harness_process_id_and_needs_the_header(stub):
    import os

    assert (await stub.get("/__test__/alive")).status_code == 403
    response = await stub.get("/__test__/alive", headers=TEST)
    assert response.status_code == 200
    assert response.json() == {"pid": os.getpid()}


async def test_the_alive_endpoint_is_get_only_and_not_a_sentinel_contact(stub, state):
    assert (await stub.post("/__test__/alive", headers=TEST)).status_code in (404, 405)
    await stub.get("/__test__/alive", headers=TEST)
    assert state.sentinel_hits == 0
