import unittest
from observa_investigation import InvestigationConfig, Orchestrator
from observa_investigation.agents.base import AgentResult, AgentTask
from observa_investigation.contracts import (
    EvidenceLink,
    FeedbackIntent,
    FollowUpRequest,
    InvestigationResult,
    RequestedChange,
    SignalFinding,
)

INCIDENT = {
    "id": "INC-PASS-C",
    "snapshot_id": "r1",
    "first_observed_at": "2026-11-17T10:00:00Z",
    "last_observed_at": "2026-11-17T10:05:00Z",
    "affected_services": ["orders-service", "payments-service"],
}


class MockSignalAgent:
    def __init__(self, signal: str):
        self.signal = signal
        self.call_count = 0
        self.last_objectives = []

    def run(self, task: AgentTask) -> AgentResult:
        self.call_count += 1
        self.last_objectives = list(task.objectives)
        fid = f"INC-PASS-C:{self.signal}:F1"
        link = EvidenceLink(
            evidence_id=f"INC-PASS-C/r1/{self.signal}#1",
            relation="supports",
            reference_valid=True,
            relevance_status="relevant",
            support_status="supported",
        )
        finding = SignalFinding(
            finding_id=fid,
            signal_type=self.signal,
            service="payments-service",
            claim=f"{self.signal} observed finding",
            claim_kind="observation",
            evidence_links=[link],
            execution_status="completed",
            uncertainty=None,
        )
        return AgentResult(status="completed", findings=[finding])


class PassCTests(unittest.TestCase):
    def setUp(self):
        self.logs_agent = MockSignalAgent("logs")
        self.metrics_agent = MockSignalAgent("metrics")
        self.traces_agent = MockSignalAgent("traces")
        self.agents = {
            "logs": self.logs_agent,
            "metrics": self.metrics_agent,
            "traces": self.traces_agent,
        }
        self.config = InvestigationConfig(max_reinvestigations=2, max_review_cycles=3)
        self.orch = Orchestrator(self.agents, self.config)

    def test_initial_investigation_runs_all_agents(self):
        res = self.orch.investigate(INCIDENT)
        self.assertEqual(res.status, "completed")
        self.assertIsNone(res.parent_run_id)
        self.assertEqual(self.logs_agent.call_count, 1)
        self.assertEqual(self.metrics_agent.call_count, 1)
        self.assertEqual(self.traces_agent.call_count, 1)

    def test_targeted_follow_up_runs_only_targeted_agent(self):
        initial = self.orch.investigate(INCIDENT)

        req = FollowUpRequest(
            request_id="REQ-1",
            incident_id="INC-PASS-C",
            origin="correlation",
            target_agents=["metrics"],
            objectives=["Deep dive into database connection pool metrics"],
            source_run_id=initial.run_id,
        )

        res = self.orch.follow_up(req, INCIDENT, prior_result=initial)
        self.assertEqual(res.status, "completed")
        self.assertEqual(res.parent_run_id, initial.run_id)
        self.assertEqual(req.status, "completed")

        # Metrics agent ran again (count 2), while logs and traces were preserved (count 1)!
        self.assertEqual(self.metrics_agent.call_count, 2)
        self.assertEqual(self.logs_agent.call_count, 1)
        self.assertEqual(self.traces_agent.call_count, 1)
        self.assertIn("Deep dive into database connection pool metrics", self.metrics_agent.last_objectives)

    def test_follow_up_limit_exhaustion_raises(self):
        initial = self.orch.investigate(INCIDENT)
        req = FollowUpRequest(
            request_id="REQ-2",
            incident_id="INC-PASS-C",
            origin="correlation",
            target_agents=["logs"],
            objectives=["Re-check error logs"],
            source_run_id=initial.run_id,
        )
        with self.assertRaises(ValueError) as ctx:
            self.orch.follow_up(req, INCIDENT, prior_result=initial, reinvestigation_count=2)
        self.assertIn("RETRY_LIMIT_REACHED", str(ctx.exception))
        self.assertEqual(req.status, "rejected")

    def test_feedback_intent_with_inconclusive_does_not_reinvestigate(self):
        initial = self.orch.investigate(INCIDENT)
        intent = FeedbackIntent(
            feedback_id="FB-1",
            incident_id="INC-PASS-C",
            report_id="RPT-1",
            report_version=1,
            human_feedback="Not sure about this, maybe look again?",
            agent_status="inconclusive",  # ambiguous intent!
            review_cycle_id="CYCLE-1",
        )
        res = self.orch.handle_feedback(intent, INCIDENT, prior_result=initial)
        # Does NOT trigger re-investigation:
        self.assertEqual(self.logs_agent.call_count, 1)
        self.assertEqual(self.metrics_agent.call_count, 1)
        self.assertEqual(self.traces_agent.call_count, 1)
        self.assertEqual(res.run_id, initial.run_id)

    def test_feedback_intent_with_specific_request_runs_targeted_agent(self):
        initial = self.orch.investigate(INCIDENT)
        intent = FeedbackIntent(
            feedback_id="FB-2",
            incident_id="INC-PASS-C",
            report_id="RPT-1",
            report_version=1,
            human_feedback="Please verify traces propagation for payments",
            requested_changes=[
                RequestedChange(target="traces", action="re-verify", reason="confirm propagation")
            ],
            agent_status="completed",
            review_cycle_id="CYCLE-2",
        )
        res = self.orch.handle_feedback(intent, INCIDENT, prior_result=initial)
        self.assertEqual(res.parent_run_id, initial.run_id)
        # Traces ran again (count 2), logs and metrics kept (count 1)
        self.assertEqual(self.traces_agent.call_count, 2)
        self.assertEqual(self.logs_agent.call_count, 1)
        self.assertEqual(self.metrics_agent.call_count, 1)


if __name__ == "__main__":
    unittest.main()
