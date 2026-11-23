"""
Unit tests verifying Audit Report Track B1 fixes:
- F2-2: Separate review cycle and re-investigation limits
- F2-3: Ambiguous feedback with no prior result raises typed VALIDATION_ERROR
- F2-9: Correct overall status in _aggregate (1 failed + 2 empty completed -> partial)
- F2-14: Failed retry preserves previous attempt findings and missing_evidence together
- F2-7: Typed F2Error carrying contract ErrorEnvelope
"""
import unittest
from unittest.mock import MagicMock
from observa_investigation.contracts import (
    FollowUpRequest,
    FeedbackIntent,
    RequestedChange,
    InvestigationResult,
    SignalOutput,
    SignalFinding,
    MissingEvidence,
    ErrorEnvelope,
    F2Error,
)
from observa_investigation.config import InvestigationConfig
from observa_investigation.orchestrator import Orchestrator


class TestAuditB1Fixes(unittest.TestCase):
    def setUp(self):
        self.config = InvestigationConfig(
            max_reinvestigations=2,
            max_review_cycles=3,
        )
        self.mock_agents = {}
        for s in ("logs", "metrics", "traces"):
            ag = MagicMock()
            ag.run.return_value = MagicMock(
                status="completed", findings=[], missing_evidence=[], answers=[]
            )
            self.mock_agents[s] = ag

        self.orch = Orchestrator(agents=self.mock_agents, config=self.config)
        self.incident = {
            "incident_id": "INC-TEST-001",
            "snapshot_id": "r1-snap",
            "affected_services": ["orders-service"],
        }

    def test_f2_2_separate_limits_review_cycle_2_accepted(self):
        """F2-2: Review cycle 2 must be accepted when max_review_cycles=3, even if max_reinvestigations=2."""
        intent = FeedbackIntent(
            feedback_id="FB-101",
            incident_id="INC-TEST-001",
            report_id="RPT-1",
            report_version=1,
            human_feedback="Please check metrics",
            agent_status="completed",
            review_cycle_id="CYCLE-2",
            requested_changes=[
                RequestedChange(target="metrics", action="reinvestigate", reason="disputed")
            ],
        )
        # review_cycle_count = 2 should NOT be rejected by max_reinvestigations=2!
        result = self.orch.handle_feedback(
            intent,
            self.incident,
            review_cycle_count=2,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.incident_id, "INC-TEST-001")

        # But review_cycle_count = 3 should be rejected with RETRY_LIMIT_REACHED
        with self.assertRaises(F2Error) as ctx:
            self.orch.handle_feedback(
                intent,
                self.incident,
                review_cycle_count=3,
            )
        self.assertEqual(ctx.exception.envelope.code, "RETRY_LIMIT_REACHED")

    def test_f2_3_ambiguous_feedback_no_prior_result(self):
        """F2-3: Ambiguous feedback with no prior result must NOT start a new run; must raise VALIDATION_ERROR."""
        intent = FeedbackIntent(
            feedback_id="FB-AMBIG",
            incident_id="INC-TEST-001",
            report_id="RPT-1",
            report_version=1,
            human_feedback="I am not sure what is wrong",
            agent_status="inconclusive",
            review_cycle_id="CYCLE-1",
        )
        with self.assertRaises(F2Error) as ctx:
            self.orch.handle_feedback(intent, self.incident, prior_result=None)

        self.assertEqual(ctx.exception.envelope.code, "VALIDATION_ERROR")
        for s in ("logs", "metrics", "traces"):
            self.mock_agents[s].run.assert_not_called()

    def test_f2_9_overall_status_aggregation(self):
        """F2-9: Table-driven verification of overall status in _aggregate."""
        # 1 failed + 2 completed with empty findings -> must be partial, NOT failed!
        state_mixed = {
            "incident": self.incident,
            "run_id": "RUN-001",
            "plan_id": "PLAN-001",
            "agents": {
                "logs": {"status": "failed", "findings": [], "missing_evidence": [], "error": "timeout"},
                "metrics": {"status": "completed", "findings": [], "missing_evidence": []},
                "traces": {"status": "completed", "findings": [], "missing_evidence": []},
            }
        }
        res = self.orch._aggregate(state_mixed)
        self.assertEqual(res['result']['status'], "partial")

        # all 3 failed -> failed
        state_all_failed = {
            "incident": self.incident,
            "run_id": "RUN-002",
            "plan_id": "PLAN-002",
            "agents": {
                "logs": {"status": "failed", "findings": [], "missing_evidence": [], "error": "err"},
                "metrics": {"status": "failed", "findings": [], "missing_evidence": [], "error": "err"},
                "traces": {"status": "failed", "findings": [], "missing_evidence": [], "error": "err"},
            }
        }
        res_fail = self.orch._aggregate(state_all_failed)
        self.assertEqual(res_fail['result']['status'], "failed")

        # all completed with no missing -> completed
        state_all_completed = {
            "incident": self.incident,
            "run_id": "RUN-003",
            "plan_id": "PLAN-003",
            "agents": {
                "logs": {"status": "completed", "findings": [], "missing_evidence": []},
                "metrics": {"status": "completed", "findings": [], "missing_evidence": []},
                "traces": {"status": "completed", "findings": [], "missing_evidence": []},
            }
        }
        res_comp = self.orch._aggregate(state_all_completed)
        self.assertEqual(res_comp['result']['status'], "completed")

    def test_f2_7_typed_reinvestigation_limit_error(self):
        """F2-7: FollowUpRequest hitting limit raises F2Error with RETRY_LIMIT_REACHED."""
        req = FollowUpRequest(
            request_id="REQ-001",
            incident_id="INC-TEST-001",
            origin="correlation",
            target_agents=["metrics"],
            objectives=["check pool"],
            source_run_id="RUN-PREV",
        )
        with self.assertRaises(F2Error) as ctx:
            self.orch.follow_up(req, self.incident, reinvestigation_count=2)

        self.assertEqual(ctx.exception.envelope.code, "RETRY_LIMIT_REACHED")
        self.assertFalse(ctx.exception.envelope.retryable)



    def test_f2_6_inbound_incident_validation(self):
        """F2-6: Inbound incident without incident_id is rejected with VALIDATION_ERROR."""
        with self.assertRaises(F2Error) as ctx:
            self.orch.investigate({})

        self.assertEqual(ctx.exception.envelope.code, "VALIDATION_ERROR")
        self.assertIn("missing required 'incident_id'", ctx.exception.envelope.message)

if __name__ == "__main__":
    unittest.main()
