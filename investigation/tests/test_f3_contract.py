import json
import unittest
from pathlib import Path

from observa_investigation.contracts import (
    FollowUpRequest,
    InvestigationResult,
    SignalFinding,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class F3ContractTests(unittest.TestCase):
    def test_completed_fixture_strict_validation(self):
        p = FIXTURES_DIR / "investigation_result_completed.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        res = InvestigationResult.model_validate(data)

        self.assertEqual(res.status, "completed")
        self.assertEqual(res.incident_id, "INC-5ea3c95c57204f69b5c9")
        self.assertEqual(len(res.missing_evidence), 0)

        # Check all slots present
        self.assertEqual(res.logs_output.status, "completed")
        self.assertEqual(res.metrics_output.status, "completed")
        self.assertEqual(res.traces_output.status, "completed")

        # Verify evidence links format
        all_findings = (
            res.logs_output.findings
            + res.metrics_output.findings
            + res.traces_output.findings
        )
        for f in all_findings:
            self.assertIn(f.claim_kind, ("observation", "inference"))
            for link in f.evidence_links:
                self.assertTrue("/" in link.evidence_id and "#" in link.evidence_id)
                if f.claim_kind == "observation":
                    self.assertEqual(link.support_status, "supported")
                else:
                    self.assertEqual(link.support_status, "unverified")

    def test_partial_fixture_strict_validation(self):
        p = FIXTURES_DIR / "investigation_result_partial.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        res = InvestigationResult.model_validate(data)

        self.assertEqual(res.status, "partial")
        self.assertTrue(len(res.missing_evidence) > 0)
        self.assertEqual(res.missing_evidence[0].status, "unavailable")

    def test_f3_can_generate_valid_followup_request_from_gap(self):
        p = FIXTURES_DIR / "investigation_result_partial.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        res = InvestigationResult.model_validate(data)

        gap = res.missing_evidence[0]
        req = FollowUpRequest(
            request_id="REQ-F3-01",
            incident_id=res.incident_id,
            origin="correlation",
            target_agents=[gap.requested_from],
            objectives=[f"Acquire missing {gap.required}: {gap.reason}"],
            evidence_needed=[gap.required],
            source_run_id=res.run_id,
        )
        self.assertEqual(req.origin, "correlation")
        self.assertEqual(req.status, "pending")
        self.assertEqual(req.target_agents, ["logs"])


if __name__ == "__main__":
    unittest.main()
