import unittest

from observa_investigation import InvestigationConfig, Orchestrator
from observa_investigation.agents.base import AgentTask, UnavailableAgent
from observa_investigation.agents.traces import TracesSignalAgent, finding_to_agent_result

try:
    from traces_agent.llm import ScriptedLLM
except ImportError:
    ScriptedLLM = None

ID = {"incident_id": "INC-T", "snapshot_id": "r1-t"}
T1 = {
    "id": "T1",
    "type": "failed_span",
    "service": "payments-service",
    "operation": "POST /payments/charge",
    "status": 402,
    "count": 12,
    "p95_duration_ms": 3.69,
    "max_duration_ms": 3.69,
    "is_deepest_origin": True,
}
LIMITS = [
    "sample of 20 retained traces (476 spans); traces represent bounded snapshots, not continuous distributed trace history"
]


def finding(status="ok", suspected="payments-service", evidence=(T1,), **kw):
    f = {
        **ID,
        "signal": "traces",
        "status": status,
        "suspected_service": suspected,
        "hypothesis": "payments-service failed with 402, propagating to orders-service.",
        "confidence": "high",
        "evidence": list(evidence),
        "caveats": ["traces do not prove root cause"],
        "limits": LIMITS,
        "root_cause": "not_determined",
    }
    f.update(kw)
    return f


def profile(count=20, missing=(), gaps=()):
    return {
        "sample": {"traces_count": count},
        "services_missing": list(missing),
        "read_gaps": list(gaps),
    }


class TracesMappingTests(unittest.TestCase):
    def test_ok_finding_becomes_observations_plus_one_inference(self):
        r = finding_to_agent_result(finding(), profile())
        self.assertEqual(r.status, "completed")
        self.assertEqual([f.claim_kind for f in r.findings], ["observation", "inference"])
        obs, inf = r.findings[0], r.findings[1]
        self.assertEqual(obs.evidence_links[0].evidence_id, "INC-T/r1-t/traces#T1")
        self.assertEqual(obs.evidence_links[0].support_status, "supported")
        self.assertIn("deepest failure origin", obs.claim)
        self.assertEqual(inf.service, "payments-service")

    def test_no_evidence_is_inconclusive(self):
        f = finding(status="no_evidence", suspected=None, evidence=(), caveats=[])
        r = finding_to_agent_result(f, profile(0, missing=["payments-service"]))
        self.assertEqual((r.status, r.findings), ("inconclusive", []))


if __name__ == "__main__":
    unittest.main()
