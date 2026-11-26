"""F1, F2, F3 and F4 connection points.

F1 tests read F1's real example incidents on ``main`` (``alert-detection-grouping/examples/
live-docker/``) and F2 tests read F2's example results (``investigation/fixtures/``), both
read-only. Memory itself holds only F5's fake, synthetic approvals.
"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from observa.f5_memory import IncidentMemory, MemoryOperationError
from observa.f5_memory.f1_adapter import alert_findings_from_f1, incident_context_from_f1
from observa.f5_memory.f2_adapter import check_memory_for_investigation, findings_from_investigation
from observa.f5_memory.f3_adapter import f3_memory_lookup
from observa.f5_memory.integration import similar_incidents_for_correlation_result
from observa.f5_memory.relevance import KeywordScorer
from observa.f5_memory.sources import FakeApprovalSource

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"
F1_LIVE = sorted((ROOT / "alert-detection-grouping" / "examples" / "live-docker").glob("*/*.json"))
F2_EXAMPLES = sorted((ROOT / "investigation" / "fixtures").glob("investigation_result_*.json"))
APPROVED = ("DEC-201-v2", "DEC-202-v1", "DEC-203-v1", "DEC-204-v1", "DEC-205-v1", "DEC-207-v1", "DEC-208-v1")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class MemoryCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "memory.sqlite3"
        self.memory = IncidentMemory(self.path, FakeApprovalSource.from_file(FIXTURES / "approval_snapshots.json"))
        for decision_id in APPROVED:
            self.memory.save_approved_incident(decision_id)
        self.current = load(FIXTURES / "current_incidents.json")

    def assert_error(self, code, fn):
        with self.assertRaises(MemoryOperationError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)


@unittest.skipUnless(F1_LIVE, "F1's live examples are not checked out (they are on main)")
class RealF1Tests(MemoryCase):
    def test_every_real_f1_incident_maps_to_incident_context(self):
        for path in F1_LIVE:
            with self.subTest(path=path.name):
                raw = load(path)
                context = incident_context_from_f1(raw)
                self.assertEqual(context["incident_id"], raw["id"])
                self.assertEqual(context["end_time"], raw.get("resolved_at"))
                self.assertEqual(context["alerts"], sorted(raw["alerts"]))
                findings = alert_findings_from_f1(raw)
                self.assertEqual(len(findings), len(raw["alerts"]))
                self.assertTrue(all(f["indicator"] == raw["alerts"][a]["rule"]
                                    for f, a in zip(findings, sorted(raw["alerts"]))))

    def test_open_incident_keeps_unknown_end_time(self):
        open_ones = [p for p in F1_LIVE if load(p)["status"] == "open"]
        self.assertTrue(open_ones)
        self.assertIsNone(incident_context_from_f1(load(open_ones[0]))["end_time"])

    def test_check_memory_runs_on_real_f1_context_and_alerts(self):
        for path in F1_LIVE:
            with self.subTest(path=path.name):
                raw = load(path)
                result = self.memory.check_memory(incident_context_from_f1(raw), alert_findings_from_f1(raw))
                self.assertEqual(result["status"], "ok")
                for item in result["items"]:
                    self.assertEqual(item["knowledge_origin"], "historical")
                    self.assertIn("comparison", item)

    def test_real_context_with_monitoring_pseudo_services(self):
        raw = copy.deepcopy(load(F1_LIVE[0]))
        raw["affected_services"] = [*raw["affected_services"], "observability:prometheus", "observability:coverage"]
        context = incident_context_from_f1(raw)
        self.assertIn("observability:coverage", context["affected_services"])
        result = self.memory.check_memory(context, alert_findings_from_f1(raw))
        self.assertEqual(result["status"], "ok")
        for item in result["items"]:
            basis = item["relevance_basis"]
            self.assertFalse(any(s.startswith("observability") for s in basis["matched_services"] + basis["current_only_services"]))


class RelevanceRuleTests(unittest.TestCase):
    """Rules learned from F1's real examples; they need no files."""

    def test_f1_title_wording_alone_is_not_a_match(self):
        record = {"incident_context": {"title": "Observed payment symptoms in payments-service",
                                       "affected_services": ["payments-service"]},
                  "findings": [], "final_root_cause": None, "verified_root_cause": None, "accepted_recommendations": []}
        self.assertIsNone(KeywordScorer(min_matched_terms=1).score(
            "Observed database symptoms in orders-service HTTP 5xx ratio", ["orders-service"], record))

    def test_written_out_service_names_do_not_count_twice(self):
        record = {"incident_context": {"title": "x", "affected_services": ["payments-service"]},
                  "findings": [{"claim": "payments-service logs show payment_failed", "indicator": None}],
                  "final_root_cause": None, "verified_root_cause": None, "accepted_recommendations": []}
        scorer = KeywordScorer(min_matched_terms=1)
        self.assertIsNone(scorer.score("payments-service", ["payments-service"], record))
        basis = scorer.score("payments-service payment_failed", ["payments-service"], record)
        self.assertIn("payment", basis["matched_terms"])
        self.assertNotIn("service", basis["matched_terms"])


@unittest.skipUnless(F2_EXAMPLES, "F2's example results are not checked out (they are on main)")
class F2Tests(MemoryCase):
    def test_real_f2_results_become_query_findings(self):
        for path in F2_EXAMPLES:
            with self.subTest(path=path.name):
                result = load(path)
                findings = findings_from_investigation(result)
                expected = sum(len(result[f"{s}_output"]["findings"]) for s in ("logs", "metrics", "traces"))
                self.assertEqual(len(findings), expected)
                self.assertTrue(all(f["claim"] and f["signal_type"] in ("logs", "metrics", "traces") for f in findings))

    def test_check_memory_for_investigation_uses_f2_findings(self):
        result = load(F2_EXAMPLES[0])
        incident = {"incident_id": result["incident_id"], "title": "Payment attempts failing during checkout",
                    "affected_services": ["payments-service", "orders-service"]}
        outcome = check_memory_for_investigation(self.memory, incident, result)
        self.assertEqual(outcome["status"], "ok")
        self.assertIn("INC-202", [i["source_incident_id"] for i in outcome["items"]])

    def test_pydantic_like_model_is_accepted(self):
        result = load(F2_EXAMPLES[0])

        class Model:
            def model_dump(self, mode):
                return copy.deepcopy(result)

        self.assertEqual(findings_from_investigation(Model()), findings_from_investigation(result))

    def test_mismatched_or_malformed_investigation_is_rejected(self):
        result = load(F2_EXAMPLES[0])
        self.assert_error("VALIDATION_ERROR", lambda: check_memory_for_investigation(
            self.memory, {"incident_id": "OTHER", "title": "t", "affected_services": []}, result))
        broken = copy.deepcopy(result)
        del broken["metrics_output"]
        self.assert_error("VALIDATION_ERROR", lambda: findings_from_investigation(broken))
        broken = copy.deepcopy(result)
        broken["logs_output"]["findings"][0]["claim"] = "  "
        self.assert_error("VALIDATION_ERROR", lambda: findings_from_investigation(broken))


def f3_check_memory(report, lookup):
    """Mirror of F3's ``check_memory`` on main (``correlation_agent/agent/tools.py``)."""
    if lookup is None:
        return []
    services = {f["service"] for s in ("logs", "metrics", "traces") for f in report[f"{s}_output"]["findings"]}
    return lookup(report["incident_id"], sorted(services))


class F3AdapterTests(MemoryCase):
    def setUp(self):
        super().setUp()
        current = self.current["payments"]
        self.incident = {k: v for k, v in current.items() if k != "findings"}
        finding = {"finding_id": "C:logs:F1", "signal_type": "logs", "service": "payments-service",
                   "claim": current["findings"][0], "claim_kind": "observation", "indicator": "payment_unavailable",
                   "evidence_links": [], "execution_status": "completed", "uncertainty": None}
        empty = {"answers": [], "findings": [], "status": "completed"}
        self.report = {"incident_id": self.incident["incident_id"], "run_id": "R", "investigation_plan_id": "P",
                       "logs_output": {**empty, "findings": [finding]}, "metrics_output": empty,
                       "traces_output": empty, "missing_evidence": [], "status": "completed", "parent_run_id": None}

    def test_fits_f3_signature_and_returns_labelled_strings(self):
        lookup = f3_memory_lookup(self.memory, {"incident": self.incident, "investigation": self.report})
        history = f3_check_memory(self.report, lookup)
        self.assertTrue(history and all(isinstance(line, str) for line in history))
        self.assertIn("INC-202", history[0])
        self.assertTrue(history[0].startswith("[HISTORICAL, human-approved]"))
        self.assertIn("not evidence for the current incident", history[0])
        self.assertEqual(lookup.last_result["status"], "ok")

    def test_context_can_be_a_callable_per_incident(self):
        lookup = f3_memory_lookup(self.memory, lambda incident_id: {"incident": self.incident, "investigation": self.report})
        self.assertTrue(f3_check_memory(self.report, lookup))

    def test_without_context_there_is_no_query_and_no_match(self):
        lookup = f3_memory_lookup(self.memory)
        self.assertEqual(f3_check_memory(self.report, lookup), [])
        self.assertEqual(lookup.last_result["status"], "ok")
        self.assertIn("no query text", lookup.last_result["note"])

    def test_failure_returns_empty_list_and_keeps_the_error(self):
        lookup = f3_memory_lookup(self.memory, {"incident": self.incident, "investigation": self.report})
        self.path.unlink()
        self.assertEqual(f3_check_memory(self.report, lookup), [])
        self.assertEqual(lookup.last_result["status"], "failed")
        self.assertEqual(lookup.last_result["error"]["code"], "STORAGE_FAILURE")

    def test_bad_input_never_raises_into_f3(self):
        lookup = f3_memory_lookup(self.memory, {"incident": {**self.incident, "incident_id": "OTHER"}})
        self.assertEqual(lookup(self.incident["incident_id"], ["payments-service"]), [])
        self.assertEqual(lookup.last_result["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(f3_memory_lookup(self.memory, {"incident": self.incident})(self.incident["incident_id"], "x"), [])
        self.assertEqual(f3_memory_lookup(self.memory, {"broken": 1})(self.incident["incident_id"], []), [])

    def test_lookup_is_read_only(self):
        before = self.memory.list_approved_versions("INC-202")
        f3_memory_lookup(self.memory, {"incident": self.incident, "investigation": self.report})("X", [])
        self.assertEqual(self.memory.list_approved_versions("INC-202"), before)


class F4ConnectionTests(MemoryCase):
    def test_similar_incidents_from_f3_public_result(self):
        incident = {k: v for k, v in self.current["pool"].items() if k != "findings"}
        correlation = {"incident_id": incident["incident_id"], "run_id": "F3-RUN", "leading_candidate_id": "C1",
                       "root_cause_candidates": [{"candidate_id": "C1", "description": "Orders DB connection pool exhausted",
                                                  "evidence_links": [], "uncertainty": None}],
                       "missing_evidence": [], "status": "completed", "follow_up_requests": []}
        result = similar_incidents_for_correlation_result(self.memory, incident, correlation)
        top = result["items"][0]
        self.assertEqual(top["source_incident_id"], "INC-201")
        rec = top["accepted_recommendations"][0]
        self.assertEqual((rec["execution_status"], rec["resolution_status"]), ("unknown", "unknown"))
        self.assertIn("validation", rec)

    def test_no_leading_cause_and_wrong_incident(self):
        incident = {k: v for k, v in self.current["unrelated"].items() if k != "findings"}
        correlation = {"incident_id": incident["incident_id"], "leading_candidate_id": None, "root_cause_candidates": []}
        self.assertEqual(similar_incidents_for_correlation_result(self.memory, incident, correlation)["items"], [])
        self.assert_error("VALIDATION_ERROR", lambda: similar_incidents_for_correlation_result(
            self.memory, incident, {**correlation, "incident_id": "OTHER"}))


class StoryGapTests(MemoryCase):
    def test_ikm04_comparison_lists_similarities_and_differences(self):
        incident = {k: v for k, v in self.current["payments"].items() if k != "findings"}
        findings = [{"claim": "payment_unavailable responses with status 503", "indicator": "payment_unavailable",
                     "signal_type": "logs"},
                    {"claim": "checkout latency rising", "indicator": "latency", "signal_type": "metrics"}]
        item = next(i for i in self.memory.check_memory(incident, findings)["items"] if i["source_incident_id"] == "INC-202")
        comparison = item["comparison"]
        self.assertIn("payment_unavailable", comparison["indicators"]["shared"])
        self.assertIn("latency", comparison["indicators"]["current_only"])
        self.assertIn("payment_failures", comparison["indicators"]["historical_only"])
        self.assertEqual(comparison["signals_with_findings"]["current"], ["logs", "metrics"])
        self.assertIn("not evidence", comparison["note"])

    def test_ikm05_unavailable_evidence_is_listed(self):
        self.assertEqual(len(self.memory.list_approved_versions("INC-204")[0]["unavailable_evidence_ids"]), 2)
        self.assertEqual(len(self.memory.list_approved_versions("INC-207")[0]["unavailable_evidence_ids"]), 1)
        self.assertEqual(self.memory.list_approved_versions("INC-202")[0]["unavailable_evidence_ids"], [])

    def test_optional_rule_store_only_ended_incidents(self):
        strict = IncidentMemory(Path(self.path).with_name("strict.sqlite3"),
                                FakeApprovalSource.from_file(FIXTURES / "approval_snapshots.json"),
                                require_ended_incident=True)
        self.assert_error("NOT_ELIGIBLE", lambda: strict.save_approved_incident("DEC-204-v1"))
        self.assertTrue(strict.save_approved_incident("DEC-201-v2")["created"])
        self.assertEqual(len(self.memory.list_approved_versions("INC-204")), 1)  # default: approval alone
        self.assert_error("VALIDATION_ERROR", lambda: IncidentMemory(self.path, require_ended_incident="yes"))


if __name__ == "__main__":
    unittest.main()
