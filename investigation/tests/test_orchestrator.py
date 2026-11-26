import unittest

from observa_investigation import InvestigationConfig, Orchestrator
from observa_investigation.agents.base import AgentResult, UnavailableAgent
from tests.helpers import INCIDENT, ScriptedAgent, finding, ok


def make(logs=None, metrics=None, traces=None, **cfg):
    agents = {"logs": logs or ScriptedAgent("logs", ok("logs")),
              "metrics": metrics or ScriptedAgent("metrics", ok("metrics")),
              "traces": traces or ScriptedAgent("traces", ok("traces"))}
    return Orchestrator(agents, InvestigationConfig(**cfg)), agents


class OrchestratorTests(unittest.TestCase):
    def test_initial_investigation_collects_all_three(self):
        orch, agents = make()
        r = orch.investigate(INCIDENT, run_id="RUN-9", plan_id="PLAN-9")
        self.assertEqual((r.status, r.run_id, r.investigation_plan_id, r.parent_run_id), ("completed", "RUN-9", "PLAN-9", None))
        for out in (r.logs_output, r.metrics_output, r.traces_output):
            self.assertEqual((out.status, len(out.findings)), ("completed", 1))
        self.assertEqual(r.missing_evidence, [])
        t = agents["logs"].tasks[0]
        self.assertEqual((t.attempt, t.run_id, t.snapshot_id), (1, "RUN-9", "r1-abc"))
        self.assertIn("orders-service", t.objectives[0])

    def test_generated_ids_when_not_given(self):
        r = make()[0].investigate(INCIDENT)
        self.assertTrue(r.run_id.startswith("RUN-") and r.investigation_plan_id.startswith("PLAN-"))

    def test_failed_agent_is_retried_then_succeeds(self):
        metrics = ScriptedAgent("metrics", RuntimeError("boom"), ok("metrics"))
        r = make(metrics=metrics)[0].investigate(INCIDENT)
        self.assertEqual((metrics.calls, [t.attempt for t in metrics.tasks]), (2, [1, 2]))
        self.assertEqual((r.status, r.metrics_output.status), ("completed", "completed"))

    def test_only_failed_agents_are_retried(self):
        orch, agents = make(traces=ScriptedAgent("traces", RuntimeError("x"), ok("traces")))
        orch.investigate(INCIDENT)
        self.assertEqual((agents["logs"].calls, agents["metrics"].calls, agents["traces"].calls), (1, 1, 2))

    def test_retry_limit_exhausted_gives_partial_and_keeps_other_results(self):
        metrics = ScriptedAgent("metrics", RuntimeError("down"))
        r = make(metrics=metrics, max_retries=2)[0].investigate(INCIDENT)
        self.assertEqual(metrics.calls, 3)  # first try + 2 retries, then stop
        self.assertEqual((r.status, r.metrics_output.status, r.metrics_output.findings), ("partial", "failed", []))
        self.assertEqual(len(r.logs_output.findings), 1)
        gap = [m for m in r.missing_evidence if m.requested_from == "metrics"]
        self.assertEqual((len(gap), gap[0].status), (1, "failed"))
        self.assertIn("down", gap[0].reason)

    def test_zero_retries_means_one_attempt(self):
        traces = ScriptedAgent("traces", RuntimeError("x"))
        make(traces=traces, max_retries=0)[0].investigate(INCIDENT)
        self.assertEqual(traces.calls, 1)

    def test_all_agents_failed_is_failed_with_three_slots(self):
        agents = {s: ScriptedAgent(s, RuntimeError("x")) for s in ("logs", "metrics", "traces")}
        r = Orchestrator(agents, InvestigationConfig(max_retries=0)).investigate(INCIDENT)
        self.assertEqual((r.status, r.logs_output.status, r.metrics_output.status, r.traces_output.status),
                         ("failed", "failed", "failed", "failed"))

    def test_timeout_counts_as_failure(self):
        slow = ScriptedAgent("metrics", (0.5, ok("metrics")))
        r = make(metrics=slow, agent_timeout_s=0.1, max_retries=0)[0].investigate(INCIDENT)
        self.assertEqual((r.status, r.metrics_output.status), ("partial", "failed"))
        self.assertIn("timed out", r.missing_evidence[0].reason)

    def test_retry_replaces_findings_and_does_not_double_add(self):
        failing_with_finding = AgentResult(status="failed", findings=[finding("logs", 1)], error="half done")
        logs = ScriptedAgent("logs", failing_with_finding, ok("logs", 2))
        r = make(logs=logs)[0].investigate(INCIDENT)
        self.assertEqual([f.finding_id for f in r.logs_output.findings], ["INC-1:logs:F2"])

    def test_partial_results_survive_when_agent_keeps_failing(self):
        logs = ScriptedAgent("logs", AgentResult(status="failed", findings=[finding("logs")], error="half"),
                             RuntimeError("down"))
        r = make(logs=logs)[0].investigate(INCIDENT)
        self.assertEqual((r.logs_output.status, len(r.logs_output.findings), r.status), ("partial", 1, "partial"))

    def test_inconclusive_agent_records_missing_evidence_even_if_it_did_not(self):
        logs = ScriptedAgent("logs", AgentResult(status="inconclusive"))
        r = make(logs=logs)[0].investigate(INCIDENT)
        self.assertEqual((r.status, logs.calls), ("partial", 1))  # inconclusive is a valid answer, not retried
        self.assertEqual([m.requested_from for m in r.missing_evidence], ["logs"])

    def test_agent_cannot_return_another_signals_findings(self):
        bad = ScriptedAgent("logs", AgentResult(status="completed", findings=[finding("logs", signal_type="metrics")]))
        r = make(logs=bad, max_retries=0)[0].investigate(INCIDENT)
        self.assertEqual((r.logs_output.status, r.logs_output.findings), ("failed", []))
        self.assertIn("contract violation", r.missing_evidence[0].reason)

    def test_placeholder_agents_report_missing_evidence_not_facts(self):
        agents = {"logs": ScriptedAgent("logs", ok("logs")),
                  "metrics": UnavailableAgent("metrics", "not built"), "traces": UnavailableAgent("traces", "not built")}
        r = Orchestrator(agents).investigate(INCIDENT)
        self.assertEqual((r.status, r.metrics_output.findings, r.traces_output.findings), ("partial", [], []))
        self.assertEqual({m.requested_from for m in r.missing_evidence}, {"metrics", "traces"})

    def test_missing_agent_or_incident_id_rejected(self):
        with self.assertRaises(ValueError):
            Orchestrator({"logs": ScriptedAgent("logs", ok("logs"))})
        with self.assertRaises(ValueError):
            make()[0].investigate({"snapshot_id": "x"})

    def test_result_follows_contract_shape(self):
        d = make()[0].investigate(INCIDENT).model_dump()
        self.assertEqual(set(d), {"incident_id", "run_id", "logs_output", "metrics_output", "traces_output",
                                  "missing_evidence", "status", "investigation_plan_id", "parent_run_id"})
        self.assertEqual(set(d["logs_output"]), {"answers", "findings", "status"})


if __name__ == "__main__":
    unittest.main()
