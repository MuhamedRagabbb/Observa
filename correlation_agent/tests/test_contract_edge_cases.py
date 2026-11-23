"""Map fault-specific F3 decisions to the public CorrelationResult contract.

Each test builds F2 observation rows directly and drives the deterministic
decision path (resolve -> to_contract_result), so no model call is involved.
"""
import unittest

from observa_investigation.contracts import InvestigationResult, SignalOutput

from correlation_agent.agent.contract import to_contract_result
from correlation_agent.agent.state import ContractCorrelationResult, IncidentHistory, Observation, OpeningAlert
from correlation_agent.agent.tools import EvidenceRequest, Hypothesis, RequestEvidence, SubmitCause, resolve

INCIDENT = "INC-0123456789abcdef0123"
SNAPSHOT = "r1-" + "a" * 64
CONTRACT_FIELDS = {"incident_id", "run_id", "root_cause_candidates", "leading_candidate_id",
                   "missing_evidence", "status", "follow_up_requests"}


def investigation(run_id="RUN-f2-edge"):
    empty = SignalOutput(status="completed")
    return InvestigationResult(incident_id=INCIDENT, run_id=run_id, logs_output=empty, metrics_output=empty,
                               traces_output=empty, status="completed", investigation_plan_id="PLAN-edge")


def row(signal, number, service, indicator, claim, snapshot=SNAPSHOT):
    prefix = {"logs": "P", "metrics": "M", "traces": "T"}[signal]
    return Observation(finding_id=f"{INCIDENT}:{signal}:F{number}", signal=signal, service=service,
                       indicator=indicator, claim=claim, snapshot_id=snapshot,
                       evidence_ids=[f"{INCIDENT}/{snapshot}/{signal}#{prefix}{number}"])


def history(service, rule, family):
    return IncidentHistory(opening_alert=OpeningAlert(service=service, rule=rule, family=family),
                           phase="initial", first_snapshot=SNAPSHOT, snapshot_id=SNAPSHOT,
                           first_observed_at="2026-11-23T14:31:15Z", resolved_at=None,
                           affected_services=[service], peaks=[], dependencies=[], release_findings=[])


def links_of(public: dict) -> dict:
    leading = next(c for c in public["root_cause_candidates"] if c["candidate_id"] == public["leading_candidate_id"])
    return {link["evidence_id"]: link for link in leading["evidence_links"]}


class DbConnectionExhaustionContractTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            row("logs", 1, "orders-service", "db_pool_timeout",
                "orders-service logged 12 'db_pool_timeout' failure records in the retained sample"),
            row("metrics", 1, "orders-service", "pool_pressure",
                "orders-service breached pool_pressure (database) with trigger value 1.0 vs threshold 0.9 ratio"),
            row("metrics", 2, "orders-service", "pool_timeouts",
                "orders-service breached pool_timeouts (database) with trigger value 9 vs threshold 2"),
        ]
        self.history = history("orders-service", "pool_timeouts", "database")

    def test_cause_citing_pool_occupancy_is_a_completed_contract_result(self):
        decision = SubmitCause(candidates=[Hypothesis(
            service="orders-service", explanation="The database pool was exhausted: occupancy reached capacity.",
            supporting_rows=[1, 2], related_rows=[])], effects=[])
        public = to_contract_result(resolve(decision, investigation(), self.rows, [], self.history)).model_dump()

        self.assertEqual(set(public), CONTRACT_FIELDS)
        self.assertEqual(public["status"], "completed")
        self.assertIsNotNone(public["leading_candidate_id"])
        self.assertEqual(public["follow_up_requests"], [])
        self.assertEqual(set(links_of(public)), {
            f"{INCIDENT}/{SNAPSHOT}/logs#P1", f"{INCIDENT}/{SNAPSHOT}/metrics#M1"})
        for link in links_of(public).values():
            self.assertEqual((link["relation"], link["support_status"], link["reference_valid"]),
                             ("supports", "supported", True))

    def test_exhaustion_claim_without_occupancy_data_becomes_a_partial_pool_request(self):
        rows = [self.rows[0], self.rows[2]]  # acquisition failures only, no pool_pressure row
        decision = SubmitCause(candidates=[Hypothesis(
            service="orders-service", explanation="Pool saturation exhausted every connection.",
            supporting_rows=[1, 2], related_rows=[])], effects=[])
        public = to_contract_result(resolve(decision, investigation(), rows, [], self.history)).model_dump()

        self.assertEqual(public["status"], "partial")
        self.assertIsNone(public["leading_candidate_id"])
        self.assertEqual(public["root_cause_candidates"], [])
        self.assertEqual(len(public["follow_up_requests"]), 1)
        request = public["follow_up_requests"][0]
        self.assertEqual(request["target_agents"], ["metrics"])
        self.assertIn("Active and maximum database pool connections for orders-service",
                      request["evidence_needed"])
        self.assertIn("Active and maximum database pool connections for orders-service",
                      [gap["required"] for gap in public["missing_evidence"]])


class BadDeploymentContractTests(unittest.TestCase):
    def test_release_change_followed_by_failures_is_cited_as_supporting_evidence(self):
        rows = [
            row("logs", 1, "orders-service", "release_changed",
                "orders-service recorded release_changed to 2.4.1 at 2026-11-23T14:30:00Z; "
                "first retained OrderStateWriteError on that release at 2026-11-23T14:30:20Z."),
            row("metrics", 1, "orders-service", "persistence_errors",
                "orders-service breached persistence_errors (regression) with trigger value 6 vs threshold 2"),
            row("traces", 1, "orders-service", "POST /orders",
                "orders-service observed 4 'failed_span' events for 'POST /orders', HTTP 500"),
        ]
        decision = SubmitCause(candidates=[Hypothesis(
            service="orders-service", explanation="Release 2.4.1 introduced failing order writes.",
            supporting_rows=[1, 2, 3], related_rows=[])], effects=[])
        result = resolve(decision, investigation(), rows, [],
                         history("orders-service", "persistence_errors", "regression"))
        public = to_contract_result(result).model_dump()

        self.assertEqual(public["status"], "completed")
        self.assertEqual(len(public["root_cause_candidates"]), 1)
        candidate = public["root_cause_candidates"][0]
        self.assertEqual(candidate["candidate_id"], public["leading_candidate_id"])
        self.assertIsNone(candidate["uncertainty"])
        self.assertTrue(candidate["description"].startswith("orders-service: "))
        self.assertIn(f"{INCIDENT}/{SNAPSHOT}/logs#P1", links_of(public))
        self.assertEqual(len(links_of(public)), 3)

    def test_identical_input_yields_identical_run_and_candidate_ids(self):
        rows = [row("logs", 1, "orders-service", "release_changed", "release_changed to 2.4.1"),
                row("metrics", 1, "orders-service", "persistence_errors", "persistence_errors breached")]
        decision = SubmitCause(candidates=[Hypothesis(service="orders-service", explanation="Bad release.",
                                                      supporting_rows=[1, 2], related_rows=[])], effects=[])
        hist = history("orders-service", "persistence_errors", "regression")
        first = to_contract_result(resolve(decision, investigation(), rows, [], hist))
        second = to_contract_result(resolve(decision, investigation(), rows, [], hist))
        self.assertEqual((first.run_id, first.leading_candidate_id), (second.run_id, second.leading_candidate_id))


class InconclusiveContractTests(unittest.TestCase):
    def request(self):
        return RequestEvidence(requests=[EvidenceRequest(
            signal="traces", required="Failed call path into payments-service",
            reason="No signal identifies the initiating service.")], candidates=[])

    def test_stopped_follow_up_with_no_candidate_is_inconclusive_with_null_leader(self):
        result = resolve(self.request(), investigation(), [], [])
        result.report.follow_up_stop_reason = "repeated_request"
        public = to_contract_result(result).model_dump()

        self.assertEqual(public["status"], "inconclusive")
        self.assertIsNone(public["leading_candidate_id"])
        self.assertEqual(public["root_cause_candidates"], [])
        self.assertNotIn("error", public)
        self.assertIn("Failed call path into payments-service",
                      [gap["required"] for gap in public["missing_evidence"]])
        ContractCorrelationResult.model_validate(public)

    def test_open_follow_up_with_no_candidate_is_partial(self):
        public = to_contract_result(resolve(self.request(), investigation(), [], [])).model_dump()
        self.assertEqual(public["status"], "partial")
        self.assertIsNone(public["leading_candidate_id"])
        self.assertEqual([r["target_agents"] for r in public["follow_up_requests"]], [["traces"]])


if __name__ == "__main__":
    unittest.main()
