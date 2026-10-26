import asyncio
import json
import time

import httpx
import pytest
from pydantic import ValidationError

from controllers.fault_injector.app import Activation, AutoConfig, Controller, create_app
from services.shared.errors import AppError
from services.shared.models import RuntimeUpdate
from services.shared.runtime import Runtime


@pytest.fixture
async def controller(tmp_path):
    calls = []

    def target(request):
        calls.append(request)
        return httpx.Response(200, json={"status": "ok"})

    instance = Controller(
        tmp_path / "incidents.jsonl", httpx.AsyncClient(transport=httpx.MockTransport(target))
    )
    await instance.open()
    yield instance, calls
    await instance.close()


@pytest.mark.parametrize(
    "config",
    [
        {"scenario": "unknown"},
        {"scenario": "payments_errors", "duration_seconds": 1801},
        {"scenario": "payments_errors", "parameters": {"failure_rate": 1.1}},
        {"scenario": "payments_errors", "parameters": {"status_code": 450}},
        {"scenario": "bad_deployment", "parameters": {"delay_seconds": 5}},
        {"scenario": "db_connection_exhaustion", "parameters": {"hold_seconds": 20}},
        {"scenario": "bad_deployment", "parameters": {"secret": "value"}},
    ],
)
def test_controller_validation(config):
    with pytest.raises(ValidationError):
        Activation(**config)


async def test_ttl_conflicts_idempotency_and_reactivation(controller):
    ctl, calls = controller
    config = Activation(scenario="payments_errors", duration_seconds=0.15, parameters={"failure_rate": 1})
    await ctl.activate(config)
    with pytest.raises(AppError) as exc:
        await ctl.activate(Activation(scenario="bad_deployment"))
    assert exc.value.status == 409
    assert "scenario" not in json.loads(calls[-1].content)
    await asyncio.sleep(0.22)
    assert ctl.active is None
    assert ctl.journal.records[0]["ended_at"]
    await ctl.deactivate("payments_errors")
    await ctl.activate(config)
    await ctl.deactivate("payments_errors")
    assert len(ctl.journal.records) == 2
    assert len(ctl.journal.path.read_text().splitlines()) == 2


async def test_auto_excludes_manual_and_cleans_up(controller):
    ctl, _ = controller
    await ctl.start_auto(AutoConfig(warmup_seconds=0, duration_seconds=20, recovery_seconds=0))
    await asyncio.sleep(0.02)
    assert ctl.active
    with pytest.raises(AppError) as exc:
        await ctl.deactivate(ctl.active["scenario"])
    assert exc.value.code == "auto_running"
    await ctl.stop_auto()
    assert ctl.active is None and not ctl.status()["auto_running"]


async def test_auto_rotates_deterministically(controller):
    ctl, _ = controller
    config = AutoConfig(warmup_seconds=0, duration_seconds=0.1, recovery_seconds=0, shuffle=True, seed=12)
    await ctl.start_auto(config)
    await asyncio.wait_for(ctl.auto_task, 2)
    assert len(ctl.journal.records) == 3
    assert {record["scenario"] for record in ctl.journal.records} == set(config.scenarios)
    assert all(record["ended_at"] for record in ctl.journal.records)


async def test_target_unavailable_stays_locked_until_lease_expires(tmp_path):
    def unavailable(request):
        raise httpx.ConnectError("unavailable")

    ctl = Controller(
        tmp_path / "incidents.jsonl", httpx.AsyncClient(transport=httpx.MockTransport(unavailable))
    )
    ctl.expiry_task = asyncio.create_task(ctl.expire())
    with pytest.raises(AppError):
        await ctl.activate(Activation(scenario="payments_errors", duration_seconds=0.1))
    assert ctl.active["status"] == "activation_unconfirmed"
    await asyncio.sleep(0.2)
    assert ctl.active is None
    await ctl.close()


async def test_controller_api_contract_and_defaults(tmp_path):
    app = create_app(
        path=tmp_path / "incidents.jsonl",
        client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://controller") as client,
    ):
        assert (await client.post("/faults/activate", json={"scenario": "unknown"})).status_code == 422
        result = await client.post("/faults/activate", json={"scenario": "payments_errors"})
        assert result.status_code == 200
        assert result.json()["active"]["scenario"] == "payments_errors"
        assert "root_cause_service" not in result.json()["active"]  # ground truth stays in the journal
        assert (await client.post("/faults/activate", json={"scenario": "bad_deployment"})).status_code == 409
        assert (
            await client.post("/faults/deactivate", json={"scenario": "payments_errors"})
        ).status_code == 200


async def test_target_ttl_and_deactivation_release_actual_holders():
    class Tel:
        def log(self, *args, **kwargs):
            pass

    runtime = Runtime(Tel())
    await runtime.update(RuntimeUpdate(expires_at=time.time() + 0.1, db_hold_seconds=10))
    start = time.perf_counter()
    await runtime.hold()
    await asyncio.sleep(0.02)
    assert runtime.settings is None
    assert time.perf_counter() - start < 0.3
    await runtime.update(RuntimeUpdate(expires_at=time.time() + 30, db_hold_seconds=10))
    task = asyncio.create_task(runtime.hold())
    await asyncio.sleep(0.01)
    await runtime.update(None)
    await asyncio.wait_for(task, 0.2)
    await runtime.close()
