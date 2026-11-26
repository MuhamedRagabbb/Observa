"""Feature 1 incident shape -> Feature 5 incident context. Synthetic fixture only."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from observa.f5_memory import IncidentMemory, MemoryOperationError
from observa.f5_memory.f1_adapter import incident_context_from_f1
from observa.f5_memory.sources import FakeApprovalSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class F1AdapterTests(unittest.TestCase):
    def setUp(self):
        sample = json.loads((FIXTURES / "f1_incident_sample.json").read_text())
        self.assertTrue(sample["synthetic"])
        self.open = sample["incidents"]["open"]
        self.resolved = sample["incidents"]["resolved"]

    def assert_invalid(self, incident):
        with self.assertRaises(MemoryOperationError) as raised:
            incident_context_from_f1(incident)
        self.assertEqual(raised.exception.code, "VALIDATION_ERROR")

    def test_open_incident_maps_to_diagram_names_with_unknown_end_time(self):
        self.assertEqual(incident_context_from_f1(self.open), {
            "incident_id": "INC-F1-SAMPLE-OPEN",
            "title": "Observed database symptoms in orders-service",
            "alerts": ["f1alert-a1", "f1alert-b2"],
            "start_time": "2026-11-04T09:00:00.250000Z",
            "end_time": None,
            "affected_services": ["orders-service", "api-gateway"],
        })

    def test_resolved_incident_uses_resolved_at_as_end_time(self):
        context = incident_context_from_f1(self.resolved)
        self.assertEqual(context["end_time"], "2026-11-04T10:05:00Z")
        self.assertEqual(context["alerts"], ["f1alert-c3"])

    def test_older_1_0_layout_is_accepted(self):
        incident = {**self.resolved, "schema_version": "1.0"}
        self.assertEqual(incident_context_from_f1(incident)["incident_id"], "INC-F1-SAMPLE-RESOLVED")

    def test_input_is_not_mutated_and_output_is_independent(self):
        before = copy.deepcopy(self.open)
        context = incident_context_from_f1(self.open)
        context["affected_services"].append("changed")
        self.assertEqual(self.open, before)

    def test_f1_root_cause_and_hypotheses_are_not_carried_over(self):
        context = incident_context_from_f1(self.open)
        self.assertEqual(set(context), {"incident_id", "title", "alerts", "start_time", "end_time", "affected_services"})

    def test_missing_summary_is_an_error_not_an_invented_title(self):
        incident = copy.deepcopy(self.open)
        del incident["summary"]
        self.assert_invalid(incident)
        self.assert_invalid({**self.open, "summary": "  "})

    def test_malformed_incidents_are_rejected(self):
        self.assert_invalid("not an object")
        self.assert_invalid({**self.open, "schema_version": "3.0"})
        self.assert_invalid({k: v for k, v in self.open.items() if k != "schema_version"})
        self.assert_invalid({k: v for k, v in self.open.items() if k != "id"})
        self.assert_invalid({k: v for k, v in self.open.items() if k != "first_observed_at"})
        self.assert_invalid({**self.open, "alerts": ["f1alert-a1"]})
        self.assert_invalid({**self.open, "alerts": {"f1alert-a1": {"id": "other"}}})
        self.assert_invalid({**self.open, "affected_services": "orders-service"})
        self.assert_invalid({**self.open, "first_observed_at": "2026-11-04 09:00"})
        self.assert_invalid({**self.resolved, "resolved_at": "2026-11-04T09:59:00Z"})

    def test_adapted_context_works_with_check_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = IncidentMemory(Path(directory) / "memory.sqlite3",
                                    FakeApprovalSource.from_file(FIXTURES / "approval_snapshots.json"))
            for decision_id in ("DEC-201-v2", "DEC-202-v1", "DEC-203-v1"):
                self.assertEqual(memory.save_approved_incident(decision_id)["status"], "stored")
            findings = ["db_pool_timeout errors in orders-service logs", "db.pool.acquire.duration is rising"]
            result = memory.check_memory(incident_context_from_f1(self.open), findings)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["items"][0]["source_incident_id"], "INC-201")
        self.assertEqual(result["items"][0]["knowledge_origin"], "historical")


if __name__ == "__main__":
    unittest.main()
