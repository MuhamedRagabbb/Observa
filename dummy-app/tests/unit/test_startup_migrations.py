"""Startup cannot expose checkout before all three databases are prepared."""
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
import yaml

from scripts import migrate_all


@pytest.fixture
def setup_inputs(tmp_path):
    env = {}
    for service in migrate_all.SERVICES:
        folder = tmp_path / service
        folder.mkdir()
        (folder / "001_test.sql").write_text("SELECT 1;")
        env[service.upper() + "_DATABASE_URL"] = "secret-" + service
    return env, tmp_path


async def test_setup_preserves_service_order_and_closes_connections(monkeypatch, setup_inputs):
    env, root = setup_inputs
    connections = [AsyncMock() for _ in migrate_all.SERVICES]
    connect = AsyncMock(side_effect=connections)
    apply = AsyncMock(side_effect=[["001_test"], [], ["001_test"]])
    monkeypatch.setattr(migrate_all.asyncpg, "connect", connect)
    monkeypatch.setattr(migrate_all, "apply_migrations", apply)
    await migrate_all.migrate_databases(env, root)
    assert [call.args[0] for call in connect.await_args_list] == ["secret-" + s for s in migrate_all.SERVICES]
    assert [call.args[1] for call in apply.await_args_list] == [root / s for s in migrate_all.SERVICES]
    assert all(call.kwargs["server_settings"]["lock_timeout"] == "5s" for call in connect.await_args_list)
    for connection in connections:
        connection.close.assert_awaited_once()


async def test_failure_stops_before_later_databases_and_hides_credentials(monkeypatch, setup_inputs):
    env, root = setup_inputs
    connect = AsyncMock(return_value=AsyncMock())
    apply = AsyncMock(side_effect=[[], asyncpg.PostgresError("secret-payments")])
    monkeypatch.setattr(migrate_all.asyncpg, "connect", connect)
    monkeypatch.setattr(migrate_all, "apply_migrations", apply)
    with pytest.raises(RuntimeError, match="failed for payments") as error:
        await migrate_all.migrate_databases(env, root)
    assert "secret-payments" not in str(error.value)
    assert connect.await_count == 2
    assert connect.return_value.close.await_count == 2


async def test_missing_input_fails_before_any_database_changes(monkeypatch, setup_inputs):
    env, root = setup_inputs
    del env["ORDERS_DATABASE_URL"]
    connect = AsyncMock()
    monkeypatch.setattr(migrate_all.asyncpg, "connect", connect)
    with pytest.raises(RuntimeError, match="Missing database configuration for orders"):
        await migrate_all.migrate_databases(env, root)
    connect.assert_not_awaited()


def test_business_startup_is_gated_and_grafana_is_in_default_stack():
    root = Path(__file__).resolve().parents[2]
    services = yaml.safe_load((root / "docker-compose.yml").read_text())["services"]
    # The temporary initialization server accepts Unix sockets before TCP is ready.
    assert "-h 127.0.0.1" in services["postgres"]["healthcheck"]["test"][1]
    setup = services["db-migrate"]
    assert setup["restart"] == "no" and setup["healthcheck"]["disable"]
    assert setup["networks"] == ["business"]
    assert setup["depends_on"]["postgres"]["condition"] == "service_healthy"
    for service in ("inventory", "payments", "orders"):
        assert services[service]["depends_on"]["db-migrate"]["condition"] == "service_completed_successfully"
    assert not services["grafana"].get("profiles")
