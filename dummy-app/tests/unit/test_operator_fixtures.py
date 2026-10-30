"""P5-0: the Operator Dashboard's mocked browser tests build controller answers from
web/e2e/fixtures/controller-facts.json. Its facts must be the controller's own, so a mock can
never show a state the real controller would not: which controls are enabled in each state (and
why not), and which services the health check reports."""

import json
from pathlib import Path

import pytest

from controllers.fault_injector.app import BLOCKED_BY, Controller
from controllers.fault_injector.health import HEALTH_SERVICES

FACTS = json.loads(
    (Path(__file__).resolve().parents[2] / "web" / "e2e" / "fixtures" / "controller-facts.json").read_text(
        "utf-8"
    )
)
STATES = ["idle", "manual", "automatic", "recovering", "unknown"]


@pytest.mark.parametrize("state", STATES)
def test_the_controls_of_each_state_are_the_controllers(state):
    assert FACTS["controls"][state] == Controller._controls(state, auto_running=state == "automatic")


def test_every_state_is_covered_and_every_blocking_reason_is_a_real_one():
    assert sorted(FACTS["controls"]) == sorted(STATES)
    assert set(BLOCKED_BY) <= set(STATES)


def test_the_health_services_are_the_ones_the_controller_reports_in_its_order():
    assert FACTS["health_services"] == list(HEALTH_SERVICES)
