"""Guards that keep fault scenarios and the reconciler away from the live stack by default."""

from pathlib import Path

import yaml

from tests.scenarios.test_live_scenarios import LIVE_PROJECT, fault_target_refusal

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"


def test_fault_scenarios_are_refused_unless_explicitly_aimed_at_a_throwaway_project():
    assert fault_target_refusal({}) is not None
    assert fault_target_refusal({"RUN_FAULT_TESTS": "1"}) is not None  # project not named
    assert fault_target_refusal({"RUN_FAULT_TESTS": "1", "COMPOSE_PROJECT_NAME": "observa-dummy"}) is not None
    assert fault_target_refusal({"COMPOSE_PROJECT_NAME": "observa-faulttest"}) is not None  # not opted in
    assert fault_target_refusal({"RUN_FAULT_TESTS": "1", "COMPOSE_PROJECT_NAME": "observa-faulttest"}) is None


def test_the_guarded_project_name_is_the_live_compose_project():
    assert yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["name"] == LIVE_PROJECT


def test_the_journal_folder_defaults_to_the_live_ground_truth_and_can_be_redirected():
    controller = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["fault-injector"]
    assert controller["volumes"] == ["${GROUND_TRUTH_DIR:-./ground-truth}:/ground-truth"]


def test_compose_passes_the_reconciler_switch_to_orders_and_defaults_it_off():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    assert services["orders"]["environment"]["RECONCILER_ENABLED"] == "${RECONCILER_ENABLED:-false}"
    others = [
        name for name, svc in services.items() if "RECONCILER_ENABLED" in (svc.get("environment") or {})
    ]
    assert others == ["orders"]
