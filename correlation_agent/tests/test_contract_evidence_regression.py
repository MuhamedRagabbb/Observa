"""Regression: a candidate's public evidence links must be exactly what it cited.

Found in the manual payments_errors run (INC-6558d5b7036a44934e9e, final report):
F2 reuses finding IDs such as ``<incident>:traces:F1`` in every snapshot, so the
opening (r1) and peak-impact (r4) analyses both contain ``traces:F1``. The final
output.json listed r4's inventory-service failed span as "supports/supported"
evidence for the payments-service cause, although the candidate cited only r1.
"""
import unittest

from correlation_agent.agent.contract import to_contract_result
from correlation_agent.agent.tools import Hypothesis, SubmitCause, resolve

from test_contract_edge_cases import INCIDENT, investigation, row

OPENING = "r1-" + "1" * 64
IMPACT = "r4-" + "4" * 64


def payments_rows():
    return [
        row("logs", 1, "payments-service", "payment_failed", "payments-service logged 37 'payment_failed'", OPENING),
        row("logs", 2, "payments-service", "request_completed", "payments-service logged 37 HTTP 503", OPENING),
        row("metrics", 1, "payments-service", "http_errors", "payments-service breached http_errors", OPENING),
        row("traces", 1, "payments-service", "POST /payments/charge", "payments-service failed_span", OPENING),
        # Same finding IDs from the impact snapshot, describing another service.
        row("traces", 1, "inventory-service", "POST /inventory/reservations",
            "inventory-service observed 15 'failed_span' events", IMPACT),
    ]


class EvidenceLinkRegressionTests(unittest.TestCase):
    def test_links_never_include_evidence_from_an_uncited_snapshot(self):
        decision = SubmitCause(candidates=[Hypothesis(service="payments-service", explanation="Charge endpoint failed.",
                                                      supporting_rows=[1, 3, 4], related_rows=[])], effects=[])
        public = to_contract_result(resolve(decision, investigation(), payments_rows(), [])).model_dump()
        linked = {link["evidence_id"] for link in public["root_cause_candidates"][0]["evidence_links"]}

        self.assertNotIn(f"{INCIDENT}/{IMPACT}/traces#T1", linked)
        self.assertEqual(linked, {f"{INCIDENT}/{OPENING}/logs#P1", f"{INCIDENT}/{OPENING}/metrics#M1",
                                  f"{INCIDENT}/{OPENING}/traces#T1"})

    def test_related_context_rows_are_not_published_as_support(self):
        decision = SubmitCause(candidates=[Hypothesis(service="payments-service", explanation="Charge endpoint failed.",
                                                      supporting_rows=[1, 3, 4], related_rows=[2])], effects=[])
        public = to_contract_result(resolve(decision, investigation(), payments_rows(), [])).model_dump()
        links = {link["evidence_id"]: link for link in public["root_cause_candidates"][0]["evidence_links"]}

        context = links[f"{INCIDENT}/{OPENING}/logs#P2"]
        self.assertNotEqual((context["relation"], context["support_status"]), ("supports", "supported"))


if __name__ == "__main__":
    unittest.main()
