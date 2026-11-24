"""Task 1.1: assemble(inputs) -> ReportContent, canonical JSON and digest (F7-US-01)."""
import json
import time
from pathlib import Path

from observa_reports.assemble import assemble
from observa_reports.content import DRAFT_NOTICE, canonical_json, digest
from observa_reports.inputs import ReportInputs

FIXTURES = Path(__file__).resolve().parents[2] / "review_service" / "fixtures"
PAYMENTS = "INC-6558d5b7036a44934e9e"


def load(group, name):
    return json.loads((FIXTURES / group / f"{name}.json").read_text(encoding="utf-8"))


def bundle(name):
    return ReportInputs.model_validate(load("bundles", name))


def test_payments_bundle_maps_every_section():
    c = assemble(bundle("payments_v1"))
    assert c.overview.incident_id == PAYMENTS
    assert c.overview.title == "Observed payment symptoms in payments-service"
    assert c.overview.end_time is None and c.overview.severity == "medium"
    assert len(c.alerts.items) == 4 and c.alerts.missing_alert_ids == []
    assert [(s, getattr(c.findings, s).status, len(getattr(c.findings, s).findings))
            for s in ("logs", "metrics", "traces")] == [("logs", "completed", 3), ("metrics", "completed", 3),
                                                       ("traces", "completed", 5)]
    corr = load("f3", "correlation_payments_completed")
    assert c.root_cause.status == "leading"
    assert c.root_cause.leading.candidate_id == corr["leading_candidate_id"]
    assert [l.relation for l in c.root_cause.leading.evidence_links] == \
        [l["relation"] for l in corr["root_cause_candidates"][0]["evidence_links"]]
    recs = load("f4", "recommendations_payments")
    assert c.recommendations.status == "ranked"
    assert [r.recommendation_id for r in c.recommendations.ranked] == recs["ranked_recommendation_ids"]
    assert [r.rank for r in c.recommendations.ranked] == [1, 2, 3]
    assert "A restart clears the symptom but not an upstream payment-provider outage." in c.recommendations.caveats
    cited = {l["evidence_id"] for l in corr["root_cause_candidates"][0]["evidence_links"]}
    assert cited <= {e.evidence_id for e in c.evidence_register}
    assert c.missing_evidence == [] and c.limitations == []
    assert "not verified" in c.executive_summary.lower()


def test_inconclusive_correlation_renders_inconclusive_not_a_cause():
    c = assemble(bundle("inconclusive"))
    assert c.root_cause.status == "inconclusive"
    assert c.root_cause.leading is None
    assert [a.uncertainty for a in c.root_cause.alternatives] == \
        ["Only log evidence supports this; no second signal type confirms it."]
    assert "no leading cause" in c.executive_summary.lower()
    assert any(m.required == "Failed call path from api-gateway to payments-service" and m.source == "F3"
               for m in c.missing_evidence)


def test_missing_f4_marks_recommendations_unavailable():
    c = assemble(bundle("missing_f4"))
    assert c.recommendations.status == "unavailable"
    assert c.recommendations.ranked == [] and c.recommendations.other_options == []
    assert "Recommendations were not provided by F4." in c.limitations


def test_empty_ranked_recommendations_is_explicit_no_validated_remediation():
    c = assemble(bundle("inconclusive"))
    assert c.recommendations.status == "no_validated_remediation"
    assert c.recommendations.ranked == []
    assert {o.action_type for o in c.recommendations.other_options} == {"verification", "investigation"}


def test_failed_f3_surfaces_error_message_in_limitations():
    c = assemble(bundle("failed_f3"))
    assert c.root_cause.status == "unavailable" and c.root_cause.leading is None
    assert any("Groq openai/gpt-oss-120b: RateLimitError (HTTP 429)" in line and "INVESTIGATION_FAILED" in line
               for line in c.limitations)


def test_missing_investigation_marks_every_signal_unavailable():
    data = load("bundles", "missing_f4")
    data["investigation"] = None
    c = assemble(ReportInputs.model_validate(data))
    assert {getattr(c.findings, s).status for s in ("logs", "metrics", "traces")} == {"unavailable"}
    assert "Signal findings were not provided by F2." in c.limitations


def test_contradicting_links_are_kept_and_labelled():
    data = {**load("bundles", "missing_f4"), "correlation": load("f3", "correlation_contradiction")}
    c = assemble(ReportInputs.model_validate(data))
    links = {l.evidence_id.rsplit("/", 1)[1]: l for l in c.root_cause.leading.evidence_links}
    assert (links["metrics#M3"].relation, links["metrics#M3"].support_status) == ("contradicts", "contradicted")
    register = {e.evidence_id.rsplit("/", 1)[1]: e for e in c.evidence_register}
    assert register["metrics#M3"].assessments[0].relation == "contradicts"
    assert register["traces#T9"].reference_status == "unresolved"
    assert register["logs#P1"].reference_status == "valid"


def test_timeline_sorted_and_untimed_events_listed_separately():
    data = load("bundles", "payments_v1")
    data["alerts"] = data["alerts"][1:]  # one grouped alert has no detail record
    c = assemble(ReportInputs.model_validate(data))
    times = [e.at for e in c.timeline.events]
    assert times == sorted(times) and all(t is not None for t in times)
    assert c.timeline.events[0].kind == "incident_started"
    missing = load("bundles", "payments_v1")["alerts"][0]["alert_id"]
    assert [e.ref for e in c.timeline.untimed] == [missing]
    assert c.alerts.missing_alert_ids == [missing]
    assert all(e.kind != "incident_ended" for e in c.timeline.events)  # ongoing: no invented end


def test_resolved_incident_ends_timeline_with_recorded_end():
    c = assemble(bundle("payments_v2_revised"))
    assert c.timeline.events[-1].kind == "incident_ended"
    assert c.timeline.events[-1].at.isoformat().replace("+00:00", "Z") == "2026-11-23T14:35:05.058730Z"


def test_claim_kinds_and_uncertainty_pass_through_verbatim():
    c = assemble(bundle("db_exhaustion"))
    inference = [f for f in c.findings.logs.findings if f.claim_kind == "inference"]
    assert [f.uncertainty for f in inference] == ["confidence: medium"]
    assert all(l.support_status == "unverified" for f in inference for l in f.evidence_links)


def test_same_inputs_same_digest():
    inputs = bundle("bad_deployment")
    first = digest(assemble(inputs))
    time.sleep(0.01)
    second = digest(assemble(ReportInputs.model_validate(load("bundles", "bad_deployment"))))
    assert first == second and len(first) == 64
    assert digest(assemble(bundle("db_exhaustion"))) != first


def test_canonical_json_is_sorted_compact_utf8_and_has_no_generated_fields():
    raw = canonical_json(assemble(bundle("payments_v1")))
    parsed = json.loads(raw.decode("utf-8"))
    assert raw == json.dumps(parsed, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    assert list(parsed) == sorted(parsed)
    assert not {"generated_at", "created_at", "report_id", "version"} & set(parsed)


def test_draft_notice_always_present():
    for name in ("payments_v1", "inconclusive", "failed_f3", "missing_f4", "db_exhaustion", "bad_deployment"):
        c = assemble(bundle(name))
        assert c.draft_notice == DRAFT_NOTICE
    assert "pending human review" in DRAFT_NOTICE.lower() and "not approved" in DRAFT_NOTICE.lower()
