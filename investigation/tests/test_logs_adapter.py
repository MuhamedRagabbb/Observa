import unittest

from observa_investigation import InvestigationConfig, Orchestrator
from observa_investigation.agents.base import AgentTask, UnavailableAgent
from observa_investigation.agents.logs import LogsSignalAgent, finding_to_agent_result

try:
    from logs_agent.llm import ScriptedLLM
except ImportError:  # the Logs Agent folder is not on PYTHONPATH
    ScriptedLLM = None

ID = {"incident_id": "INC-D", "snapshot_id": "r4-aa"}
P1 = {"id": "P1", "category": "service_failure", "service": "orders-service", "event": "order_persistence_failed",
      "error_type": "OrderPersistenceCompatibilityError", "status": None, "route": None, "count": 20,
      "window_seconds": 34.0, "rate_per_s": 0.59, "p95_duration_ms": None}
P2 = {"id": "P2", "category": "service_failure", "service": "api-gateway", "event": "dependency_request_failed",
      "error_type": None, "status": 503, "route": None, "count": 4, "window_seconds": 30.0,
      "rate_per_s": 0.13, "p95_duration_ms": None}
LIMITS = ["sample of 28 retained sanitized records, not all source logs",
          "sample starts after incident start; first_seen values are first-in-sample, not onset"]


def finding(status="ok", suspected="orders-service", evidence=(P1, P2), **kw):
    f = {**ID, "signal": "logs", "status": status, "suspected_service": suspected,
         "hypothesis": "orders-service persistence errors may explain the gateway 503s.", "confidence": "medium",
         "evidence": list(evidence), "caveats": ["No baseline rates are available for comparison"],
         "limits": LIMITS, "root_cause": "not_determined"}
    f.update(kw)
    return f


def profile(records=28, missing=(), gaps=()):
    return {"sample": {"records": records, "covers_incident_start": False},
            "services_missing": list(missing), "read_gaps": list(gaps)}


class MappingTests(unittest.TestCase):
    def test_ok_finding_becomes_observations_plus_one_inference(self):
        r = finding_to_agent_result(finding(), profile())
        self.assertEqual(r.status, "completed")
        self.assertEqual([f.claim_kind for f in r.findings], ["observation", "observation", "inference"])
        obs, inf = r.findings[0], r.findings[2]
        self.assertEqual(obs.evidence_links[0].evidence_id, "INC-D/r4-aa/logs#P1")
        self.assertEqual(obs.evidence_links[0].support_status, "supported")
        self.assertIn("20 'order_persistence_failed'", obs.claim)
        self.assertIn("first-in-sample", obs.uncertainty)
        self.assertEqual((inf.service, [l.support_status for l in inf.evidence_links]), ("orders-service", ["unverified"] * 2))
        self.assertIn("confidence: medium", inf.uncertainty)

    def test_finding_ids_are_stable_across_retries(self):
        a = finding_to_agent_result(finding(), profile())
        b = finding_to_agent_result(finding(), profile())
        self.assertEqual([f.finding_id for f in a.findings], [f.finding_id for f in b.findings])

    def test_service_without_logs_makes_result_partial_and_is_recorded(self):
        r = finding_to_agent_result(finding(evidence=(P2,), suspected="api-gateway"), profile(4, missing=["orders-service"]))
        self.assertEqual(r.status, "partial")
        self.assertEqual([m.required for m in r.missing_evidence], ["log records from orders-service"])
        self.assertTrue(all(f.execution_status == "partial" for f in r.findings))

    def test_no_evidence_is_inconclusive_with_missing_evidence(self):
        f = finding(status="no_evidence", suspected=None, evidence=(), caveats=[])
        r = finding_to_agent_result(f, profile(0, missing=["payments-service"]))
        self.assertEqual((r.status, r.findings), ("inconclusive", []))
        self.assertEqual(len(r.missing_evidence), 2)

    def test_read_failure_is_failed_so_it_can_be_retried(self):
        f = finding(status="no_evidence", suspected=None, evidence=(), caveats=[])
        r = finding_to_agent_result(f, profile(0, gaps=["logs read stopped at cursor 0: boom"]))
        self.assertEqual(r.status, "failed")
        self.assertIn("boom", r.error)

    def test_model_unavailable_keeps_observations_only(self):
        f = finding(status="llm_unavailable", suspected=None, evidence=(P1,), caveats=[])
        r = finding_to_agent_result(f, profile())
        self.assertEqual((r.status, [x.claim_kind for x in r.findings]), ("partial", ["observation"]))
        self.assertIn("not available", r.findings[0].uncertainty)


def rec(ts, service, event, level="ERROR", **kw):
    return {"timestamp": ts, "service.name": service, "event": event, "level": level, **kw}


RECORDS = [r for i in range(3) for r in (
    rec(f"2026-11-16T22:55:1{i}.000Z", "orders-service", "order_failed", **{"error.type": "payment_unavailable"}, trace_id=f"t{i}"),
    rec(f"2026-11-16T22:55:1{i}.000Z", "api-gateway", "request_completed", **{"http.status_code": 503}, trace_id=f"t{i}"))]
FULL_INCIDENT = {"incident_id": "INC-X", "snapshot_id": "r1-x", "start_time": "2026-11-16T22:52:28Z",
                 "affected_services": ["orders-service", "api-gateway", "payments-service"]}


def fake_factory(records):
    def factory(incident_id, snapshot_id):
        return lambda cursor, limit: {"items": records, "has_more": False, "total_records": len(records)}
    return factory


@unittest.skipUnless(ScriptedLLM, "logs_agent is not on PYTHONPATH")
class WithRealLogsAgentTests(unittest.TestCase):
    REPLY = ('{"suspected_service": "orders-service", "hypothesis": "orders-service reported failed orders with error '
             'type payment_unavailable, which may point at its payments dependency.", "evidence_ids": ["P1"], '
             '"confidence": "medium", "caveats": []}')

    def test_logs_agent_end_to_end_through_the_orchestrator(self):
        llm = ScriptedLLM([self.REPLY])
        logs = LogsSignalAgent("unused", llm=llm, fetcher_factory=fake_factory(RECORDS))
        agents = {"logs": logs, "metrics": UnavailableAgent("metrics", "not built"),
                  "traces": UnavailableAgent("traces", "not built")}
        r = Orchestrator(agents, InvestigationConfig(max_retries=0)).investigate(FULL_INCIDENT)
        self.assertEqual((r.status, r.logs_output.status), ("partial", "partial"))  # payments-service has no logs
        # only the row the model cited (P1) is attached as evidence
        self.assertEqual([f.claim_kind for f in r.logs_output.findings], ["observation", "inference"])
        self.assertEqual(len(llm.calls), 1)
        required = {m.required for m in r.missing_evidence}
        self.assertIn("log records from payments-service", required)
        self.assertIn("metrics analysis", required)

    def test_no_model_configured_still_returns_observations(self):
        logs = LogsSignalAgent("unused", use_llm=False, fetcher_factory=fake_factory(RECORDS))
        res = logs.run(AgentTask(incident=FULL_INCIDENT, snapshot_id="r1-x", run_id="RUN-1"))
        self.assertEqual(res.status, "partial")
        self.assertTrue(res.findings and all(f.claim_kind == "observation" for f in res.findings))

    def test_empty_snapshot_is_inconclusive(self):
        logs = LogsSignalAgent("unused", fetcher_factory=fake_factory([]))
        res = logs.run(AgentTask(incident=FULL_INCIDENT, snapshot_id="r1-x", run_id="RUN-1"))
        self.assertEqual((res.status, res.findings), ("inconclusive", []))


if __name__ == "__main__":
    unittest.main()
