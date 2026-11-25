import unittest

from observa_investigation import InvestigationConfig, Orchestrator
from observa_investigation.agents.base import AgentTask, UnavailableAgent
from observa_investigation.agents.metrics import MetricsSignalAgent, finding_to_agent_result

try:
    from metrics_agent.llm import ScriptedLLM
except ImportError:
    ScriptedLLM = None

ID = {"incident_id": "INC-D", "snapshot_id": "r4-aa"}
M1 = {
    "id": "M1",
    "service": "orders-service",
    "rule": "pool_pressure",
    "category": "resource_saturation",
    "unit": "ratio",
    "threshold": 0.9,
    "trigger_value": 0.98,
    "latest_value": 0.98,
    "deviation": 0.08,
    "severity": "critical",
    "trend": "stable",
    "window_end": "2026-11-04T10:00:00Z",
}
M2 = {
    "id": "M2",
    "service": "api-gateway",
    "rule": "http_errors",
    "category": "error_rate",
    "unit": "ratio",
    "threshold": 0.15,
    "trigger_value": 0.7,
    "latest_value": 0.7,
    "deviation": 0.55,
    "severity": "critical",
    "trend": "stable",
    "window_end": "2026-11-04T10:00:00Z",
}
LIMITS = [
    "retained sample of 8 observations across 2 services; metrics reflect alert evaluation windows and queries, not full continuous history"
]


def finding(status="ok", suspected="orders-service", evidence=(M1, M2), **kw):
    f = {
        **ID,
        "signal": "metrics",
        "status": status,
        "suspected_service": suspected,
        "hypothesis": "orders-service pool pressure of 0.98 may explain the gateway 0.7 http errors.",
        "confidence": "medium",
        "evidence": list(evidence),
        "caveats": ["metrics do not prove causation"],
        "limits": LIMITS,
        "root_cause": "not_determined",
    }
    f.update(kw)
    return f


def profile(count=8, missing=(), gaps=()):
    return {
        "sample": {"observations_count": count},
        "services_missing": list(missing),
        "read_gaps": list(gaps),
    }


class MetricsMappingTests(unittest.TestCase):
    def test_ok_finding_becomes_observations_plus_one_inference(self):
        r = finding_to_agent_result(finding(), profile())
        self.assertEqual(r.status, "completed")
        self.assertEqual([f.claim_kind for f in r.findings], ["observation", "observation", "inference"])
        obs, inf = r.findings[0], r.findings[2]
        self.assertEqual(obs.evidence_links[0].evidence_id, "INC-D/r4-aa/metrics#M1")
        self.assertEqual(obs.evidence_links[0].support_status, "supported")
        self.assertIn("breached pool_pressure", obs.claim)
        self.assertEqual((inf.service, [l.support_status for l in inf.evidence_links]), ("orders-service", ["unverified"] * 2))
        self.assertIn("confidence: medium", inf.uncertainty)

    def test_finding_ids_are_stable_across_retries(self):
        a = finding_to_agent_result(finding(), profile())
        b = finding_to_agent_result(finding(), profile())
        self.assertEqual([f.finding_id for f in a.findings], [f.finding_id for f in b.findings])

    def test_service_without_metrics_makes_result_partial(self):
        r = finding_to_agent_result(finding(evidence=(M1,), suspected="orders-service"), profile(4, missing=["payments-service"]))
        self.assertEqual(r.status, "partial")
        self.assertEqual([m.required for m in r.missing_evidence], ["metric observations from payments-service"])
        self.assertTrue(all(f.execution_status == "partial" for f in r.findings))

    def test_no_evidence_is_inconclusive_with_missing_evidence(self):
        f = finding(status="no_evidence", suspected=None, evidence=(), caveats=[])
        r = finding_to_agent_result(f, profile(0, missing=["payments-service"]))
        self.assertEqual((r.status, r.findings), ("inconclusive", []))
        self.assertEqual(len(r.missing_evidence), 2)

    def test_read_failure_is_failed_so_it_can_be_retried(self):
        f = finding(status="no_evidence", suspected=None, evidence=(), caveats=[])
        r = finding_to_agent_result(f, profile(0, gaps=["metrics read stopped at cursor 0: boom"]))
        self.assertEqual(r.status, "failed")
        self.assertIn("boom", r.error)

    def test_model_unavailable_keeps_observations_only(self):
        f = finding(status="llm_unavailable", suspected=None, evidence=(M1,), caveats=[])
        r = finding_to_agent_result(f, profile())
        self.assertEqual((r.status, [x.claim_kind for x in r.findings]), ("partial", ["observation"]))
        self.assertIn("not available", r.findings[0].uncertainty)


OBS = [
    {
        "service": "orders-service",
        "rule": "pool_pressure",
        "phase": "trigger",
        "value": 0.98,
        "threshold": 0.9,
        "unit": "ratio",
        "evidence": {"window_end": "2026-11-04T10:00:00Z", "window_seconds": 60, "query": "q", "request_count": 200},
    },
    {
        "service": "api-gateway",
        "rule": "http_errors",
        "phase": "trigger",
        "value": 0.7,
        "threshold": 0.15,
        "unit": "ratio",
        "evidence": {"window_end": "2026-11-04T10:00:00Z", "window_seconds": 60, "query": "q", "request_count": 200},
    },
]
FULL_INCIDENT = {
    "incident_id": "INC-M",
    "snapshot_id": "r1-m",
    "start_time": "2026-11-04T10:00:00Z",
    "affected_services": ["orders-service", "api-gateway", "payments-service"],
}


def fake_factory(items):
    def factory(incident_id, snapshot_id):
        return lambda cursor, limit: {
            "items": items,
            "has_more": False,
            "total_records": len(items),
            "query_window": {"start": "2026-11-04T09:58:00Z", "end": "2026-11-04T10:00:50Z"},
            "metadata": {"coverage": "complete"},
        }
    return factory


@unittest.skipUnless(ScriptedLLM, "metrics_agent is not on PYTHONPATH")
class WithRealMetricsAgentTests(unittest.TestCase):
    REPLY = (
        '{"suspected_service": "orders-service", "hypothesis": "orders-service suffered database pool pressure at 0.98, '
        'causing 0.7 http errors on api-gateway.", "evidence_ids": ["M1", "M2"], '
        '"confidence": "medium", "caveats": []}'
    )

    def test_metrics_agent_end_to_end_through_the_orchestrator(self):
        llm = ScriptedLLM([self.REPLY])
        metrics = MetricsSignalAgent("unused", llm=llm, fetcher_factory=fake_factory(OBS))
        agents = {
            "logs": UnavailableAgent("logs", "not built"),
            "metrics": metrics,
            "traces": UnavailableAgent("traces", "not built"),
        }
        r = Orchestrator(agents, InvestigationConfig(max_retries=0)).investigate(FULL_INCIDENT)
        self.assertEqual((r.status, r.metrics_output.status), ("partial", "partial"))  # payments-service has no metrics
        self.assertEqual([f.claim_kind for f in r.metrics_output.findings], ["observation", "observation", "inference"])
        self.assertEqual(len(llm.calls), 1)
        required = {m.required for m in r.missing_evidence}
        self.assertIn("metric observations from payments-service", required)
        self.assertIn("logs analysis", required)

    def test_no_model_configured_returns_observations(self):
        metrics = MetricsSignalAgent("unused", use_llm=False, fetcher_factory=fake_factory(OBS))
        res = metrics.run(AgentTask(incident=FULL_INCIDENT, snapshot_id="r1-m", run_id="RUN-1"))
        self.assertEqual(res.status, "partial")
        self.assertTrue(res.findings and all(f.claim_kind == "observation" for f in res.findings))

    def test_empty_snapshot_is_inconclusive(self):
        metrics = MetricsSignalAgent("unused", fetcher_factory=fake_factory([]))
        res = metrics.run(AgentTask(incident=FULL_INCIDENT, snapshot_id="r1-m", run_id="RUN-1"))
        self.assertEqual((res.status, res.findings), ("inconclusive", []))


if __name__ == "__main__":
    unittest.main()
