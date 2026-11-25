"""
Unit tests verifying Audit Report Track B1 and Architecture B2 fixes:
- F2-1: InvestigationService + SQLite Ledger owns the limits (caller cannot bypass)
- F2-2: Separate review cycle and re-investigation limits strictly enforced
- F2-3: Ambiguous feedback with no prior result raises typed VALIDATION_ERROR
- F2-4: Durable SQLite checkpointer supports resume(run_id)
- F2-6: IncidentV1 object accepted directly without crash (no .get() AttributeError)
- F2-7: All boundary errors are typed F2Error (VALIDATION_ERROR / RETRY_LIMIT_REACHED); caller request is immutable
- F2-9: Correct overall status in _aggregate (1 failed + 2 empty completed -> partial)
- F2-14: Failed retry preserves previous attempt findings and missing_evidence together
"""
import unittest
from unittest.mock import MagicMock

from observa_investigation.agents.base import AgentResult
from observa_investigation.checkpointer import SqliteSaver
from observa_investigation.config import InvestigationConfig
from observa_investigation.contracts import (
    ErrorEnvelope,
    EvidenceLink,
    F2Error,
    FeedbackIntent,
    FollowUpRequest,
    IncidentV1,
    InvestigationResult,
    MissingEvidence,
    RequestedChange,
    SignalFinding,
    SignalOutput,
)
from observa_investigation.orchestrator import Orchestrator
from observa_investigation.service import InvestigationLedger, InvestigationService


class TestAuditB1Fixes(unittest.TestCase):
    def setUp(self):
        self.config = InvestigationConfig(
            max_reinvestigations=2,
            max_review_cycles=3,
        )
        self.mock_agents = {}
        for s in ("logs", "metrics", "traces"):
            ag = MagicMock()
            # Return REAL AgentResult objects so result validation passes cleanly
            ag.run.return_value = AgentResult(
                status="completed", findings=[], missing_evidence=[], answers=[]
            )
            self.mock_agents[s] = ag

        self.saver = SqliteSaver(":memory:")
        self.orch = Orchestrator(agents=self.mock_agents, config=self.config, checkpointer=self.saver)
        self.incident = {
            "incident_id": "INC-TEST-001",
            "snapshot_id": "r1-snap",
            "affected_services": ["orders-service"],
            "start_time": "2026-11-01T00:00:00Z",
        }

    def tearDown(self):
        self.saver.close()

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
        service = InvestigationService(orchestrator=self.orch, ledger=InvestigationLedger(":memory:"), config=self.config)
        initial = service.investigate(self.incident)

        # 3 review cycles are allowed (max_review_cycles=3) even though max_reinvestigations=2.
        for i in range(1, 4):
            cycle = intent.model_copy(update={"feedback_id": f"FB-{100 + i}", "review_cycle_id": f"CYCLE-{i}"})
            result = service.submit_feedback(cycle, self.incident, prior_result=initial)
            self.assertEqual(result.incident_id, "INC-TEST-001")
            self.assertEqual(result.metrics_output.status, "completed")

        # The 4th is rejected by the ledger, with no count supplied by the caller.
        cycle4 = intent.model_copy(update={"feedback_id": "FB-104", "review_cycle_id": "CYCLE-4"})
        with self.assertRaises(F2Error) as ctx:
            service.submit_feedback(cycle4, self.incident, prior_result=initial)
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

    def test_f2_6_inbound_incident_validation_and_object_support(self):
        """F2-6: Inbound incident without incident_id is rejected with VALIDATION_ERROR;
        IncidentV1 objects are handled cleanly without .get() AttributeError."""
        # 1. Invalid payload rejected
        with self.assertRaises(F2Error) as ctx:
            self.orch.investigate({})
        self.assertEqual(ctx.exception.envelope.code, "VALIDATION_ERROR")

        # 2. IncidentV1 object accepted directly without crash
        inc_obj = IncidentV1(
            incident_id="INC-TEST-OBJ",
            title="Database Saturation",
            alerts=["ALT-1", "ALT-2"],
            affected_services=["orders-service"],
            start_time="2026-11-01T00:00:00Z",
            snapshot_id="r1-snap",
        )
        res = self.orch.investigate(inc_obj)
        self.assertEqual(res.incident_id, "INC-TEST-OBJ")

        # 3. Contract fields are required: an incident with only incident_id is rejected
        with self.assertRaises(F2Error) as ctx3:
            self.orch.investigate({"incident_id": "INC-ONLY-ID"})
        self.assertEqual(ctx3.exception.envelope.code, "VALIDATION_ERROR")

        # 4. Legacy field names are NOT silently translated
        with self.assertRaises(F2Error) as ctx4:
            self.orch.investigate({"id": "INC-OLD", "first_observed_at": "2026-11-01T00:00:00Z",
                                   "affected_services": ["orders-service"]})
        self.assertEqual(ctx4.exception.envelope.code, "VALIDATION_ERROR")

    def test_f2_1_orchestrator_has_no_caller_supplied_counts(self):
        """F2-1: the orchestrator exposes no count parameters, so a direct caller cannot bypass the limits."""
        import inspect
        for name in ("follow_up", "handle_feedback"):
            params = inspect.signature(getattr(Orchestrator, name)).parameters
            self.assertNotIn("review_cycle_count", params)
            self.assertNotIn("reinvestigation_count", params)

    def test_run_id_reuse_is_rejected(self):
        """Reusing a run_id must not silently return the earlier checkpointed result."""
        self.orch.investigate(self.incident, run_id="RUN-DUP")
        with self.assertRaises(F2Error) as ctx:
            self.orch.investigate(self.incident, run_id="RUN-DUP")
        self.assertEqual(ctx.exception.envelope.code, "VALIDATION_ERROR")

    def test_f2_7_typed_errors_and_caller_immutability(self):
        """F2-7: Boundary errors raise typed F2Error with ErrorEnvelope; caller request is unchanged."""
        req = FollowUpRequest(
            request_id="REQ-001",
            incident_id="INC-DIFFERENT",
            origin="correlation",
            target_agents=["metrics"],
            objectives=["check pool"],
            source_run_id="RUN-PREV",
            status="pending",
        )
        # Incident ID mismatch raises F2Error (VALIDATION_ERROR)
        with self.assertRaises(F2Error) as ctx:
            self.orch.follow_up(req, self.incident)
        self.assertEqual(ctx.exception.envelope.code, "VALIDATION_ERROR")
        # Caller request object status must NOT be modified
        self.assertEqual(req.status, "pending")

        # Invalid target agents raise F2Error (VALIDATION_ERROR)
        req_invalid = FollowUpRequest(
            request_id="REQ-002",
            incident_id="INC-TEST-001",
            origin="correlation",
            target_agents=["invalid_signal"],
            objectives=["check invalid"],
            source_run_id="RUN-PREV",
            status="pending",
        )
        with self.assertRaises(F2Error) as ctx:
            self.orch.follow_up(req_invalid, self.incident)
        self.assertEqual(ctx.exception.envelope.code, "VALIDATION_ERROR")
        self.assertEqual(req_invalid.status, "pending")

        # Feedback incident ID mismatch raises F2Error (VALIDATION_ERROR)
        intent = FeedbackIntent(
            feedback_id="FB-MISMATCH",
            incident_id="INC-DIFFERENT",
            report_id="RPT-1",
            report_version=1,
            human_feedback="Wrong ID",
            agent_status="completed",
            review_cycle_id="CYCLE-1",
        )
        with self.assertRaises(F2Error) as ctx:
            self.orch.handle_feedback(intent, self.incident)
        self.assertEqual(ctx.exception.envelope.code, "VALIDATION_ERROR")

    def test_f2_9_overall_status_aggregation(self):
        """F2-9: Table-driven verification of overall status through public investigate()."""
        # 1 failed + 2 completed with empty findings -> must be partial, NOT failed!
        self.mock_agents["logs"].run.return_value = AgentResult(status="failed", error="timeout")
        res = self.orch.investigate(self.incident)
        self.assertEqual(res.status, "partial")

        # all 3 failed -> failed
        for s in ("logs", "metrics", "traces"):
            self.mock_agents[s].run.return_value = AgentResult(status="failed", error="err")
        res_fail = self.orch.investigate(self.incident)
        self.assertEqual(res_fail.status, "failed")

        # all completed with no missing -> completed
        for s in ("logs", "metrics", "traces"):
            self.mock_agents[s].run.return_value = AgentResult(status="completed", findings=[], missing_evidence=[])
        res_comp = self.orch.investigate(self.incident)
        self.assertEqual(res_comp.status, "completed")

    def test_f2_14_failed_retry_preserves_findings_and_missing_evidence(self):
        """F2-14: When a retry fails without findings, preserve both findings and missing evidence from previous attempt."""
        prev_finding = SignalFinding(
            finding_id="INC-TEST-001:logs:F1",
            signal_type="logs",
            service="orders-service",
            claim="Observed 503 error",
            claim_kind="observation",
            evidence_links=[],
            execution_status="completed",
            uncertainty=None,
        )
        prev_missing = MissingEvidence(
            required="logs from orders-service",
            reason="truncated sample",
            requested_from="logs",
            status="unavailable",
        )

        # Attempt 1 fails WITH findings & missing_evidence; Attempt 2 (RetryPolicy) fails WITHOUT findings
        self.mock_agents["logs"].run.side_effect = [
            AgentResult(status="failed", findings=[prev_finding], missing_evidence=[prev_missing], error="initial error"),
            AgentResult(status="failed", findings=[], missing_evidence=[MissingEvidence(required="new query", reason="timeout", requested_from="logs", status="failed")], error="connection reset"),
        ]

        res = self.orch.investigate(self.incident)
        self.assertEqual(self.mock_agents["logs"].run.call_count, 2)
        self.assertEqual(len(res.logs_output.findings), 1)
        self.assertEqual(res.logs_output.findings[0].finding_id, "INC-TEST-001:logs:F1")
        self.assertTrue(any(m.required == "logs from orders-service" for m in res.missing_evidence))

    def test_f2_1_and_f2_2_service_ledger_limits_cannot_be_bypassed(self):
        """F2-1 & F2-2: InvestigationService + Ledger enforce limits without trusting caller-supplied counts."""
        ledger = InvestigationLedger(":memory:")
        service = InvestigationService(orchestrator=self.orch, ledger=ledger, config=self.config)

        # 1. Initial investigation
        initial = service.investigate(self.incident)
        self.assertIsNotNone(initial)

        # 2. Follow-up 1 (reinvestigation 1 / 2) -> Accepted
        req1 = FollowUpRequest(
            request_id="REQ-CORR-1",
            incident_id="INC-TEST-001",
            origin="correlation",
            target_agents=["metrics"],
            objectives=["check metrics pool"],
            source_run_id=initial.run_id,
        )
        res1 = service.submit_follow_up(req1, self.incident, prior_result=initial)
        self.assertIsNotNone(res1)

        # Idempotency check: submitting same request_id returns cached result
        res1_again = service.submit_follow_up(req1, self.incident, prior_result=initial)
        self.assertEqual(res1.run_id, res1_again.run_id)

        # 3. Follow-up 2 (reinvestigation 2 / 2) -> Accepted
        req2 = FollowUpRequest(
            request_id="REQ-CORR-2",
            incident_id="INC-TEST-001",
            origin="correlation",
            target_agents=["traces"],
            objectives=["check traces"],
            source_run_id=res1.run_id,
        )
        res2 = service.submit_follow_up(req2, self.incident, prior_result=res1)
        self.assertIsNotNone(res2)

        # 4. Follow-up 3 (reinvestigation 3 / 2) -> MUST BE REJECTED by Ledger!
        # Even if caller passes no count or passes 0, the ledger knows 2 reinvestigations already ran!
        req3 = FollowUpRequest(
            request_id="REQ-CORR-3",
            incident_id="INC-TEST-001",
            origin="correlation",
            target_agents=["logs"],
            objectives=["check logs"],
            source_run_id=res2.run_id,
        )
        with self.assertRaises(F2Error) as ctx:
            service.submit_follow_up(req3, self.incident, prior_result=res2)
        self.assertEqual(ctx.exception.envelope.code, "RETRY_LIMIT_REACHED")
        self.assertIn("re-investigation limit reached (2 >= 2)", ctx.exception.envelope.message)

        # 5. Feedback follow-ups sent via submit_follow_up must enforce max_review_cycles=3!
        for i in range(1, 4):
            fb_req = FollowUpRequest(
                request_id=f"REQ-FB-{i}",
                incident_id="INC-TEST-001",
                origin="human_feedback",
                target_agents=["metrics"],
                objectives=[f"review {i}"],
                source_run_id=initial.run_id,
                review_cycle_id=f"RC-{i}",
            )
            service.submit_follow_up(fb_req, self.incident, prior_result=initial)

        fb_req_4 = FollowUpRequest(
            request_id="REQ-FB-4",
            incident_id="INC-TEST-001",
            origin="human_feedback",
            target_agents=["metrics"],
            objectives=["review 4"],
            source_run_id=initial.run_id,
            review_cycle_id="RC-4",
        )
        with self.assertRaises(F2Error) as ctx_fb:
            service.submit_follow_up(fb_req_4, self.incident, prior_result=initial)
        self.assertEqual(ctx_fb.exception.envelope.code, "RETRY_LIMIT_REACHED")
        self.assertIn("review cycle limit reached (3 >= 3)", ctx_fb.exception.envelope.message)

    def test_f2_4_sqlite_saver_and_resume(self):
        """F2-4: SqliteSaver persists state and resume(run_id) restores investigation."""
        saver = SqliteSaver(":memory:")
        try:
            orch = Orchestrator(agents=self.mock_agents, config=self.config, checkpointer=saver)
            run = orch.investigate(self.incident, run_id="RUN-DURABLE-001")
            self.assertEqual(run.run_id, "RUN-DURABLE-001")

            # Resume using run_id
            resumed = orch.resume("RUN-DURABLE-001")
            self.assertEqual(resumed.run_id, "RUN-DURABLE-001")
            self.assertEqual(resumed.incident_id, "INC-TEST-001")
        finally:
            saver.close()

    def test_f2_8_and_f2_12_send_nodes_retry_policy_and_running_status(self):
        """F2-8 & F2-12: Graph has per-signal Send nodes, RetryPolicy backoff, and records running status."""
        for s in ("logs", "metrics", "traces"):
            self.assertIn(f"run_{s}", self.orch.graph.nodes)

        # Transient failure on attempt 1 -> retried by RetryPolicy -> succeeds on attempt 2
        self.mock_agents["metrics"].run.side_effect = [
            AgentResult(status="failed", error="transient 503"),
            AgentResult(status="completed", findings=[], missing_evidence=[], answers=[]),
        ]
        res = self.orch.investigate(self.incident, run_id="RUN-SEND-001")
        self.assertEqual(res.status, "completed")
        self.assertEqual(res.metrics_output.status, "completed")
        self.assertEqual(self.mock_agents["metrics"].run.call_count, 2)

        # Verify 'running' status was recorded in the checkpoint history after plan
        cfg = {"configurable": {"thread_id": "RUN-SEND-001"}}
        history = list(self.orch.graph.get_state_history(cfg))
        saw_running = any(
            snap.values.get("agents", {}).get("metrics", {}).get("status") == "running"
            for snap in history
        )
        self.assertTrue(saw_running, "Expected 'running' status to be recorded in checkpoint history")


if __name__ == "__main__":
    unittest.main()
