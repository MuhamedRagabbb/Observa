"""P5-0: web/contracts/fault-parameters.json is what the Operator Dashboard shows as fault and
cycle settings. The ranges live only in the controller's validators (OpenAPI cannot express
them), so this test checks the contract against those validators by behaviour: every bound and
default is accepted, just outside every bound is refused, and each default is what the
controller actually applies when the setting is left out."""

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from controllers.fault_injector.app import SCENARIO_NAMES, Activation, AutoConfig

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = json.loads((ROOT / "web" / "contracts" / "fault-parameters.json").read_text(encoding="utf-8"))
SCENARIOS = {scenario["id"]: scenario for scenario in CONTRACT["scenarios"]}
# What the controller sends the target service for each parameter (Activation.runtime).
RUNTIME_FLAG = {
    "hold_seconds": "db_hold_seconds",
    "failure_rate": "payment_failure_rate",
    "status_code": "payment_failure_status",
    "delay_seconds": "persistence_delay_seconds",
}
EPSILON = 1e-6


def accepted(model, **values):
    try:
        model(**values)
    except ValidationError:
        return False
    return True


def parameters():
    for scenario in CONTRACT["scenarios"]:
        for parameter in scenario["parameters"]:
            yield pytest.param(scenario["id"], parameter, id=f"{scenario['id']}.{parameter['name']}")


def test_the_scenarios_are_the_controllers_in_its_order_with_the_readme_f_numbers():
    assert [s["id"] for s in CONTRACT["scenarios"]] == list(SCENARIO_NAMES)
    assert [s["code"] for s in CONTRACT["scenarios"]] == ["F1", "F2", "F3"]
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for scenario in CONTRACT["scenarios"]:
        assert f"{scenario['code']}: `{scenario['id']}`" in readme


@pytest.mark.parametrize(("scenario", "parameter"), parameters())
def test_each_parameter_accepts_its_bounds_and_default_and_refuses_just_outside(scenario, parameter):
    name = parameter["name"]

    def ok(value):
        return accepted(Activation, scenario=scenario, parameters={name: value})

    assert ok(parameter["default"])
    if parameter["kind"] == "range":
        assert parameter["min"] <= parameter["default"] <= parameter["max"]
        assert ok(parameter["min"]) and ok(parameter["max"])
        assert not ok(parameter["min"] - EPSILON) and not ok(parameter["max"] + EPSILON)
    else:
        assert parameter["kind"] == "choice" and parameter["default"] in parameter["choices"]
        assert all(ok(choice) for choice in parameter["choices"])
        low, high = min(parameter["choices"]), max(parameter["choices"])
        for outside in (low - 1, (low + high) / 2, high + 1):
            assert not ok(outside), outside


@pytest.mark.parametrize(("scenario", "parameter"), parameters())
def test_each_default_is_what_the_controller_applies_when_the_parameter_is_left_out(scenario, parameter):
    runtime = Activation(scenario=scenario).runtime(expires_at=0)
    assert runtime[RUNTIME_FLAG[parameter["name"]]] == parameter["default"]


@pytest.mark.parametrize("scenario", SCENARIO_NAMES)
def test_a_scenario_takes_exactly_its_listed_parameters(scenario):
    own = {p["name"] for p in SCENARIOS[scenario]["parameters"]}
    others = {p["name"] for s in CONTRACT["scenarios"] for p in s["parameters"]} - own
    assert own and all(not accepted(Activation, scenario=scenario, parameters={name: 1}) for name in others)
    assert not accepted(Activation, scenario=scenario, parameters={"unlisted": 1})


@pytest.mark.parametrize("scenario", SCENARIO_NAMES)
def test_the_contract_lists_every_setting_the_controller_applies_for_a_scenario(scenario):
    # A parameter added to the controller shows up here as a runtime flag the contract lacks.
    applied = set(Activation(scenario=scenario).runtime(expires_at=0)) - {"expires_at"}
    listed = {RUNTIME_FLAG[p["name"]] for p in SCENARIOS[scenario]["parameters"]} | set(
        SCENARIOS[scenario].get("fixed", {})
    )
    assert applied == listed


def test_the_fixed_release_of_f3_is_the_one_the_controller_deploys():
    assert SCENARIOS["bad_deployment"]["fixed"] == {"release_version": "v2-buggy"}
    assert Activation(scenario="bad_deployment").runtime(expires_at=0)["release_version"] == "v2-buggy"


def test_the_manual_duration_bounds_and_default():
    duration = CONTRACT["duration_seconds"]
    assert Activation(scenario="payments_errors").duration_seconds == duration["default"]
    for value in (duration["min"], duration["max"], duration["default"]):
        assert accepted(Activation, scenario="payments_errors", duration_seconds=value)
    for value in (duration["min"] - EPSILON, duration["max"] + EPSILON):
        assert not accepted(Activation, scenario="payments_errors", duration_seconds=value)


AUTO_RANGES = ["warmup_seconds", "duration_seconds", "recovery_seconds", "repeat"]


@pytest.mark.parametrize("field", AUTO_RANGES)
def test_each_cycle_setting_accepts_its_bounds_and_default_and_refuses_just_outside(field):
    spec = CONTRACT["auto"][field]
    assert getattr(AutoConfig(), field) == spec["default"]
    step = 1 if spec.get("integer") else EPSILON
    for value in (spec["min"], spec["max"], spec["default"]):
        assert accepted(AutoConfig, **{field: value})
    for value in (spec["min"] - step, spec["max"] + step):
        assert not accepted(AutoConfig, **{field: value})
    if spec.get("integer"):
        assert not accepted(AutoConfig, **{field: spec["min"] + 0.5})


def test_the_cycle_scenarios_shuffle_and_seed():
    auto = CONTRACT["auto"]
    defaults = AutoConfig()
    assert defaults.scenarios == auto["scenarios"]["default"] == list(SCENARIO_NAMES)
    assert accepted(AutoConfig, scenarios=["payments_errors"] * auto["scenarios"]["max_items"])
    assert not accepted(AutoConfig, scenarios=["payments_errors"] * (auto["scenarios"]["max_items"] + 1))
    assert auto["scenarios"]["min_items"] == 1 and not accepted(AutoConfig, scenarios=[])
    assert defaults.shuffle is auto["shuffle"]["default"] is False
    assert defaults.seed == auto["seed"]["default"]
    assert auto["seed"]["integer"] and not accepted(AutoConfig, seed=1.5)
    # The cycle runs each fault with its default parameters: the contract offers none per scenario.
    assert set(auto) == set(AutoConfig.model_fields)  # a new cycle setting must be added here


def test_the_contract_carries_no_ground_truth_or_fault_targets():
    text = json.dumps(CONTRACT).lower()
    for forbidden in (
        "root_cause",
        "expected_signals",
        "signal",
        "target",
        "-service",
        "orders",
        "payments-",
    ):
        assert forbidden not in text, forbidden
    # Only setting names, numbers, scenario ids, F-codes and units.
    assert set(re.findall(r'"([a-z_]+)":', text)) <= {
        "version", "scenarios", "id", "code", "parameters", "name", "kind", "min", "max", "default", "unit",
        "choices", "fixed", "release_version", "duration_seconds", "auto", "warmup_seconds", "recovery_seconds",
        "repeat", "integer", "min_items", "max_items", "shuffle", "seed",
    }  # fmt: skip
