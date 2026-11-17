import json
import unittest

from metrics_agent.finding import (
    SYSTEM_PROMPT,
    _allowed_numbers,
    _bad_numbers,
    build_finding,
    extract_json,
    make_view,
    validate,
)
from metrics_agent.llm import ScriptedLLM
from metrics_agent.profiler import profile_metrics


class TestMetricsFinding(unittest.TestCase):
    def setUp(self):
        self.obs = [
            {
                "service": "orders-service",
                "rule": "pool_pressure",
                "phase": "trigger",
                "value": 0.98,
                "threshold": 0.9,
                "unit": "ratio",
                "evidence": {"window_end": "2026-11-04T10:00:00Z", "window_seconds": 60},
            },
            {
                "service": "api-gateway",
                "rule": "http_errors",
                "phase": "trigger",
                "value": 0.7,
                "threshold": 0.15,
                "unit": "ratio",
                "evidence": {"window_end": "2026-11-04T10:00:00Z", "window_seconds": 60},
            },
        ]
        self.incident = {
            "id": "INC-123",
            "snapshot_id": "r1",
            "affected_services": ["orders-service", "api-gateway"],
            "summary": "DB pool test",
        }
        self.profile = profile_metrics(self.obs, self.incident)
        self.profile["incident_id"] = "INC-123"
        self.profile["snapshot_id"] = "r1"

    def test_no_evidence(self):
        empty_prof = profile_metrics([], self.incident)
        finding = build_finding(empty_prof, self.incident, llm=None)
        self.assertEqual(finding["status"], "no_evidence")
        self.assertIsNone(finding["suspected_service"])
        self.assertEqual(finding["confidence"], "low")

    def test_llm_skipped_deterministic_fallback(self):
        finding = build_finding(self.profile, self.incident, llm=None)
        self.assertEqual(finding["status"], "llm_skipped")
        self.assertEqual(finding["suspected_service"], "orders-service")
        self.assertIn("pool_pressure", finding["hypothesis"])
        self.assertEqual(len(finding["evidence"]), 1)
        self.assertEqual(finding["evidence"][0]["rule"], "pool_pressure")

    def test_scripted_llm_ok(self):
        reply = json.dumps({
            "suspected_service": "orders-service",
            "hypothesis": "orders-service suffered database pool pressure at 0.98, leading to 0.7 http errors on api-gateway.",
            "evidence_ids": ["M1", "M2"],
            "confidence": "high",
            "caveats": ["metrics do not prove causation"],
        })
        llm = ScriptedLLM([reply])
        finding = build_finding(self.profile, self.incident, llm=llm)
        self.assertEqual(finding["status"], "ok")
        self.assertEqual(finding["suspected_service"], "orders-service")
        self.assertEqual(len(finding["evidence"]), 2)
        self.assertTrue(finding["validation"]["passed"])
        self.assertEqual(finding["validation"]["attempts"], 1)

    def test_validator_rejects_hallucinated_number(self):
        view = make_view(self.profile, self.incident)
        obj = {
            "suspected_service": "orders-service",
            "hypothesis": "orders-service pool pressure was 99.99 percent.",
            "evidence_ids": ["M1"],
            "confidence": "medium",
            "caveats": [],
        }
        errs = validate(obj, view)
        self.assertTrue(any("numbers not found" in e for e in errs))

    def test_validator_rejects_root_cause_phrase(self):
        view = make_view(self.profile, self.incident)
        obj = {
            "suspected_service": "orders-service",
            "hypothesis": "The root cause of the incident is pool pressure.",
            "evidence_ids": ["M1"],
            "confidence": "medium",
            "caveats": [],
        }
        errs = validate(obj, view)
        self.assertTrue(any("root cause" in e for e in errs))

    def test_validator_rejects_unknown_evidence_id(self):
        view = make_view(self.profile, self.incident)
        obj = {
            "suspected_service": "orders-service",
            "hypothesis": "orders-service pool pressure breached threshold.",
            "evidence_ids": ["M999"],
            "confidence": "medium",
            "caveats": [],
        }
        errs = validate(obj, view)
        self.assertTrue(any("known ids" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
