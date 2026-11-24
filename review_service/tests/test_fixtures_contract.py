"""Task 0.2: inbound contract models and the F1-F4 mock fixtures."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from observa_investigation.contracts import InvestigationResult
from observa_reports.inputs import (
    CorrelationResultIn, F1Output, IncidentIn, RecommendationResultIn, ReportInputs,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
MODELS = {"f1": F1Output, "f2": InvestigationResult, "f3": CorrelationResultIn,
          "f4": RecommendationResultIn, "bundles": ReportInputs}
EXPECTED = {
    "f1": {"incident_payments_open", "incident_payments_resolved", "incident_db_exhaustion",
           "incident_bad_deployment"},
    "f2": {"investigation_payments", "investigation_partial", "investigation_db_exhaustion",
           "investigation_bad_deployment"},
    "f3": {"correlation_payments_completed", "correlation_inconclusive", "correlation_contradiction",
           "correlation_failed", "correlation_db_exhaustion", "correlation_bad_deployment"},
    "f4": {"recommendations_payments", "recommendations_empty", "recommendations_db_exhaustion",
           "recommendations_bad_deployment"},
    "bundles": {"payments_v1", "payments_v2_revised", "db_exhaustion", "bad_deployment",
                "inconclusive", "missing_f4", "failed_f3"},
}
GROUND_TRUTH_KEYS = {"root_cause", "root_cause_service", "expected_signals", "ground_truth", "scenario"}


def load(group, name):
    return json.loads((FIXTURES / group / f"{name}.json").read_text(encoding="utf-8"))


def incident(**changes):
    base = {"incident_id": "INC-1", "title": "t", "alerts": ["A1"], "start_time": "2026-11-23T14:31:15Z",
            "end_time": None, "affected_services": ["payments-service"]}
    return {**base, **changes}


def keys_of(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys_of(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys_of(item)


@pytest.mark.parametrize("group", sorted(EXPECTED))
def test_every_fixture_validates(group):
    present = {p.stem for p in (FIXTURES / group).glob("*.json")}
    assert present == EXPECTED[group]
    for name in present:
        MODELS[group].model_validate(load(group, name))


def test_naive_timestamp_rejected():
    with pytest.raises(ValidationError, match="timezone"):
        IncidentIn.model_validate(incident(start_time="2026-11-23T14:31:15"))


def test_non_utc_offset_rejected():
    with pytest.raises(ValidationError, match="UTC"):
        IncidentIn.model_validate(incident(start_time="2026-11-23T16:31:15+02:00"))


def test_unknown_field_rejected():
    with pytest.raises(ValidationError, match="extra"):
        IncidentIn.model_validate(incident(root_cause="payments-service"))


def test_unknown_end_time_round_trips_as_null_with_z_timestamps():
    parsed = IncidentIn.model_validate(incident())
    dumped = json.loads(parsed.model_dump_json())
    assert dumped["end_time"] is None and "end_time" in dumped
    assert dumped["start_time"] == "2026-11-23T14:31:15Z"
    with pytest.raises(ValidationError):  # end_time is required-but-nullable, never silently defaulted
        IncidentIn.model_validate({k: v for k, v in incident().items() if k != "end_time"})


def test_end_time_before_start_rejected():
    with pytest.raises(ValidationError, match="end_time"):
        IncidentIn.model_validate(incident(end_time="2026-11-23T14:00:00Z"))


def test_correlation_leading_must_be_one_of_its_candidates():
    data = load("f3", "correlation_payments_completed")
    data["leading_candidate_id"] = "CAUSE-not-a-candidate"
    with pytest.raises(ValidationError, match="leading_candidate_id"):
        CorrelationResultIn.model_validate(data)


def test_failed_correlation_requires_error_and_no_leader():
    data = load("f3", "correlation_failed")
    CorrelationResultIn.model_validate(data)
    with pytest.raises(ValidationError, match="error"):
        CorrelationResultIn.model_validate({**data, "error": None})


def test_recommendation_top_must_be_first_ranked_option():
    data = load("f4", "recommendations_payments")
    data["top_recommendation_id"] = data["ranked_recommendation_ids"][-1]
    with pytest.raises(ValidationError, match="top_recommendation_id"):
        RecommendationResultIn.model_validate(data)


def test_recommendation_ranked_ids_must_exist():
    data = load("f4", "recommendations_payments")
    data["ranked_recommendation_ids"].append("REC-missing")
    with pytest.raises(ValidationError, match="ranked_recommendation_ids"):
        RecommendationResultIn.model_validate(data)


def test_bundle_rejects_parts_from_another_incident():
    bundle = load("bundles", "payments_v1")
    bundle["correlation"] = load("f3", "correlation_db_exhaustion")
    with pytest.raises(ValidationError, match="incident_id"):
        ReportInputs.model_validate(bundle)


def test_bundle_alerts_must_belong_to_the_incident():
    bundle = load("bundles", "payments_v1")
    bundle["alerts"][0]["alert_id"] = "not-in-incident"
    with pytest.raises(ValidationError, match="alert"):
        ReportInputs.model_validate(bundle)


@pytest.mark.parametrize("bundle_name, parts", [
    ("payments_v1", {"f1": "incident_payments_open", "f2": "investigation_payments",
                     "f3": "correlation_payments_completed", "f4": "recommendations_payments"}),
    ("db_exhaustion", {"f1": "incident_db_exhaustion", "f2": "investigation_db_exhaustion",
                       "f3": "correlation_db_exhaustion", "f4": "recommendations_db_exhaustion"}),
    ("bad_deployment", {"f1": "incident_bad_deployment", "f2": "investigation_bad_deployment",
                        "f3": "correlation_bad_deployment", "f4": "recommendations_bad_deployment"}),
    ("inconclusive", {"f1": "incident_payments_open", "f2": "investigation_payments",
                      "f3": "correlation_inconclusive", "f4": "recommendations_empty"}),
    ("failed_f3", {"f1": "incident_payments_open", "f2": "investigation_payments", "f3": "correlation_failed"}),
    ("missing_f4", {"f1": "incident_payments_open", "f2": "investigation_payments",
                    "f3": "correlation_payments_completed"}),
])
def test_bundles_are_built_from_the_part_fixtures(bundle_name, parts):
    bundle = load("bundles", bundle_name)
    f1 = load("f1", parts["f1"])
    assert bundle["incident"] == f1["incident"] and bundle["alerts"] == f1["alerts"]
    assert bundle["investigation"] == load("f2", parts["f2"])
    assert bundle["correlation"] == load("f3", parts["f3"])
    assert bundle["recommendations"] == (load("f4", parts["f4"]) if "f4" in parts else None)


def test_revised_bundle_is_a_new_run_of_the_same_incident():
    v1, v2 = load("bundles", "payments_v1"), load("bundles", "payments_v2_revised")
    assert v2["incident"]["incident_id"] == v1["incident"]["incident_id"]
    assert v2["investigation"]["run_id"] != v1["investigation"]["run_id"]
    assert v2["investigation"]["parent_run_id"] == v1["investigation"]["run_id"]
    assert v2["correlation"]["run_id"] != v1["correlation"]["run_id"]
    assert v2["recommendations"]["correlation_run_id"] == v2["correlation"]["run_id"]


def test_fixtures_contain_no_ground_truth_keys():
    for path in FIXTURES.rglob("*.json"):
        leaked = GROUND_TRUTH_KEYS & set(keys_of(json.loads(path.read_text(encoding="utf-8"))))
        assert not leaked, f"{path.name} leaks {leaked}"


def test_f3_fixtures_match_correlation_agent_contract():
    state = pytest.importorskip("correlation_agent.agent.state", reason="correlation_agent deps not installed")
    for name in EXPECTED["f3"]:
        state.ContractCorrelationResult.model_validate(load("f3", name))
