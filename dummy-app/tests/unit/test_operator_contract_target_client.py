# ruff: noqa: F811  (fixtures imported from the lifecycle tests are used by name, the usual pytest idiom)
"""P5-F T1: the harness must give the real controller the controller's OWN target client (no idle
connection reuse; see tests/unit/test_controller_target_transport.py), only without proxy settings, so the
contract tier exercises exactly the transport production uses. No sockets: uvicorn is faked."""

import httpx

from tests.harness import operator_contract as harness
from tests.unit.test_operator_contract_lifecycle import (  # noqa: F401  (fixtures are used by name)
    FakeConfig,
    FakeServer,
    dist,
    fakes,
    run_config,
)


async def test_the_harness_builds_the_controller_with_the_controllers_target_client(fakes, dist, monkeypatch):
    from controllers.fault_injector import app as controller_app

    made = []
    real = controller_app.target_client

    def recording(**options):
        client = real(**options)
        made.append((options, client))
        return client

    monkeypatch.setattr(controller_app, "target_client", recording)
    assert await harness.serve(run_config(dist)) == 0
    assert [options for options, _ in made] == [{"trust_env": False}]
    assert fakes[0]["client"] is made[0][1]
    assert isinstance(made[0][1], httpx.AsyncClient)
