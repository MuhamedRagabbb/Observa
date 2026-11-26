"""Small fake F3 and F4 stand-ins proving the F5 read-side connection points work."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from observa.f5_memory import IncidentMemory
from observa.f5_memory.integration import populate_correlation_state, similar_incidents_for_recommendation
from observa.f5_memory.sources import FakeApprovalSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def fake_correlation_state(current):
    incident = {k: v for k, v in current.items() if k != "findings"}
    findings = [{"finding_id": f"{incident['incident_id']}:F{n}", "claim": claim, "indicator": None}
                for n, claim in enumerate(current["findings"], 1)]
    return {"incident_context": incident, "correlated_findings": findings, "relevant_data_from_memory": None,
            "root_cause_candidates": [], "final_root_cause": None, "missing_evidence": [], "agent_status": "running"}


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "memory.sqlite3"
        snapshots = json.loads((FIXTURES / "approval_snapshots.json").read_text(encoding="utf-8"))
        self.memory = IncidentMemory(self.path, FakeApprovalSource(snapshots))
        self.current = json.loads((FIXTURES / "current_incidents.json").read_text())
        for decision_id in ("DEC-201-v2", "DEC-202-v1", "DEC-203-v1"):
            self.memory.save_approved_incident(decision_id)

    def test_f3_state_gets_memory_and_nothing_else_changes(self):
        state = fake_correlation_state(self.current["release"])
        before = copy.deepcopy(state)
        updated = populate_correlation_state(state, self.memory)
        self.assertEqual(state, before)
        self.assertEqual(updated["relevant_data_from_memory"]["status"], "ok")
        self.assertEqual(updated["relevant_data_from_memory"]["items"][0]["source_incident_id"], "INC-203")
        self.assertEqual({**updated, "relevant_data_from_memory": None}, before)
        self.assertIsNone(updated["final_root_cause"])

    def test_f3_keeps_current_evidence_when_memory_fails(self):
        state = fake_correlation_state(self.current["pool"])
        self.path.unlink()
        updated = populate_correlation_state(state, self.memory)
        self.assertEqual(updated["relevant_data_from_memory"]["status"], "failed")
        self.assertEqual(updated["relevant_data_from_memory"]["items"], [])
        self.assertEqual(updated["correlated_findings"], state["correlated_findings"])

    def test_f4_finds_past_recommendations_from_current_root_cause(self):
        for root_cause in ({"candidate_id": "C-1", "description": "Orders DB connection pool exhausted"},
                           {"service": "orders-service", "hypothesis": "Orders DB connection pool exhausted"}):
            with self.subTest(root_cause=root_cause):
                state = fake_correlation_state(self.current["pool"])
                state["final_root_cause"] = root_cause
                items = similar_incidents_for_recommendation(state, self.memory)["items"]
                self.assertEqual(items[0]["source_incident_id"], "INC-201")
                self.assertIn("pool", items[0]["accepted_recommendations"][0]["action"])


if __name__ == "__main__":
    unittest.main()
