import json
import unittest

from traces_agent.finding import (
    SYSTEM_PROMPT,
    build_finding,
    extract_json,
    make_view,
    validate,
)
from traces_agent.llm import ScriptedLLM
from traces_agent.profiler import profile_traces


class TestTracesFinding(unittest.TestCase):
    def setUp(self):
        spans = [
            {
                "span_id": "s1",
                "service": "api-gateway",
                "operation": "POST /api/orders",
                "error": True,
                "http_status_code": 500,
                "duration_us": 30000,
                "references": [],
            },
            {
                "span_id": "s2",
                "service": "payments-service",
                "operation": "POST /payments/charge",
                "error": True,
                "http_status_code": 500,
                "duration_us": 10000,
                "references": [{"refType": "CHILD_OF", "spanID": "s1"}],
            },
        ]
        self.traces = [{"trace_id": "t1", "ref_id": "r1", "spans": spans}]
        self.incident = {
            "id": "INC-T",
            "snapshot_id": "r1",
            "affected_services": ["api-gateway", "payments-service"],
            "summary": "traces test",
        }
        self.profile = profile_traces(self.traces, self.incident)
        self.profile["incident_id"] = "INC-T"
        self.profile["snapshot_id"] = "r1"

    def test_no_evidence(self):
        empty_prof = profile_traces([], self.incident)
        finding = build_finding(empty_prof, self.incident, llm=None)
        self.assertEqual(finding["status"], "no_evidence")
        self.assertIsNone(finding["suspected_service"])
        self.assertEqual(finding["confidence"], "low")

    def test_llm_skipped_fallback(self):
        finding = build_finding(self.profile, self.incident, llm=None)
        self.assertEqual(finding["status"], "llm_skipped")
        self.assertEqual(finding["suspected_service"], "payments-service")
        self.assertIn("payments-service", finding["hypothesis"])
        self.assertTrue(len(finding["evidence"]) >= 1)

    def test_scripted_llm_ok(self):
        reply = json.dumps({
            "suspected_service": "payments-service",
            "hypothesis": "payments-service failed during POST /payments/charge with 500 status.",
            "evidence_ids": ["T2"],
            "confidence": "high",
            "caveats": ["traces show correlation in request flow"],
        })
        llm = ScriptedLLM([reply])
        finding = build_finding(self.profile, self.incident, llm=llm)
        self.assertEqual(finding["status"], "ok")
        self.assertEqual(finding["suspected_service"], "payments-service")
        self.assertTrue(finding["validation"]["passed"])

    def test_validator_rejects_root_cause(self):
        view = make_view(self.profile, self.incident)
        obj = {
            "suspected_service": "payments-service",
            "hypothesis": "The root cause was payments failure.",
            "evidence_ids": ["T2"],
            "confidence": "medium",
            "caveats": [],
        }
        errs = validate(obj, view)
        self.assertTrue(any("root cause" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
