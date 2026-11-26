import json
import unittest

from logs_agent.finding import SYSTEM_PROMPT, build_finding, extract_json
from logs_agent.llm import LLMError, ScriptedLLM
from logs_agent.profiler import profile_logs
try:
    from tests.test_logs_agent import INCIDENT, sample
except ImportError:
    from test_logs_agent import INCIDENT, sample


def reply(**over):
    base = {
        "suspected_service": "payments-service",
        "hypothesis": "Orders fail because payment is unavailable; gateway errors follow the orders failures.",
        "evidence_ids": ["P1", "P2"],
        "confidence": "high",
        "caveats": ["no log records from the suspected service"],
    }
    base.update(over)
    return json.dumps(base)


class FindingTests(unittest.TestCase):
    def setUp(self):
        self.profile = profile_logs(sample(), INCIDENT)
        self.profile["incident_id"] = "INC-test"
        self.profile["snapshot_id"] = "r1-test"

    def test_happy_path_caps_confidence_and_attaches_evidence(self):
        llm = ScriptedLLM([reply()])
        f = build_finding(self.profile, INCIDENT, llm)
        self.assertEqual(f["status"], "ok")
        self.assertEqual(f["suspected_service"], "payments-service")
        self.assertEqual(f["confidence"], "medium")  # capped: sample misses the start
        self.assertTrue(any("capped" in c for c in f["caveats"]))
        self.assertEqual([e["id"] for e in f["evidence"]], ["P1", "P2"])
        self.assertEqual(f["evidence"][0]["count"], 3)  # attached by code, not by the model
        self.assertEqual(f["root_cause"], "not_determined")
        self.assertEqual(f["validation"]["attempts"], 1)
        self.assertEqual(f["incident_id"], "INC-test")

    def test_invented_number_is_rejected_then_corrected(self):
        llm = ScriptedLLM([reply(hypothesis="About 99 orders failed."), reply()])
        f = build_finding(self.profile, INCIDENT, llm)
        self.assertEqual(f["status"], "ok")
        self.assertEqual(f["validation"]["attempts"], 2)
        self.assertIn("rejected", llm.calls[1][-1]["content"])

    def test_numbers_from_the_profile_are_allowed(self):
        llm = ScriptedLLM([reply(hypothesis="3 failures at 1.5 per second in orders.")])
        self.assertEqual(build_finding(self.profile, INCIDENT, llm)["status"], "ok")

    def test_invented_service_falls_back(self):
        llm = ScriptedLLM([reply(suspected_service="billing-service")] * 2)
        f = build_finding(self.profile, INCIDENT, llm)
        self.assertEqual(f["status"], "llm_rejected")
        self.assertIsNone(f["suspected_service"])
        self.assertEqual(f["confidence"], "low")
        self.assertFalse(f["validation"]["passed"])
        self.assertTrue(f["hypothesis"])

    def test_root_cause_phrase_is_rejected(self):
        llm = ScriptedLLM([reply(hypothesis="The root cause is payments.")] * 2)
        self.assertEqual(build_finding(self.profile, INCIDENT, llm)["status"], "llm_rejected")

    def test_unknown_evidence_id_is_rejected(self):
        llm = ScriptedLLM([reply(evidence_ids=["P9"])] * 2)
        self.assertEqual(build_finding(self.profile, INCIDENT, llm)["status"], "llm_rejected")

    def test_no_records_never_calls_the_model(self):
        empty = profile_logs([], INCIDENT)
        llm = ScriptedLLM([])
        f = build_finding(empty, INCIDENT, llm)
        self.assertEqual(f["status"], "no_evidence")
        self.assertEqual(llm.calls, [])
        self.assertEqual(f["confidence"], "low")

    def test_model_error_falls_back(self):
        f = build_finding(self.profile, INCIDENT, ScriptedLLM([LLMError("HTTP 401")]))
        self.assertEqual(f["status"], "llm_unavailable")
        self.assertIn("HTTP 401", f["validation"]["errors"][0])

    def test_llm_skipped(self):
        f = build_finding(self.profile, INCIDENT, None)
        self.assertEqual(f["status"], "llm_skipped")
        self.assertEqual(f["evidence"][0]["id"], "P1")

    def test_prompt_forbids_absence_of_logs_as_evidence(self):
        self.assertIn("absence of logs", SYSTEM_PROMPT)
        self.assertIn("cross_service_traces", SYSTEM_PROMPT)

    def test_extract_json_strips_code_fences(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        with self.assertRaises(ValueError):
            extract_json("no json here")


if __name__ == "__main__":
    unittest.main()
