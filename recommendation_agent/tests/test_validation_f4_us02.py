"""Tests for F4-US-02: Validate recommendations against supporting/contradicting/expired/missing evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from observa_alerts.config import settings
from observa_alerts.demo import snapshot
from observa_alerts.engine import Engine
from observa_alerts.report import export

from recommendation_agent import (
    CatalogEvidenceResolver,
    EvidenceRef,
    F1ReportEvidenceResolver,
    IncidentWindow,
    ProposedOption,
    build_correlation_context,
    recommend_with_state,
    validate_recommendation_evidence,
)


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 25, hour, minute, 0, tzinfo=timezone.utc)


def _base_context() -> dict:
    return {
        "incident_id": "INC-val",
        "run_id": "RUN-val",
        "root_cause_candidates": [
            {
                "candidate_id": "CAUSE-pay",
                "description": "payments-service: charge errors",
                "evidence_links": [
                    {
                        "evidence_id": "INC-val/r1/logs#good",
                        "relation": "supports",
                        "reference_valid": True,
                        "relevance_status": "relevant",
                        "support_status": "supported",
                    },
                    {
                        "evidence_id": "INC-val/r1/metrics#good",
                        "relation": "supports",
                        "reference_valid": True,
                        "relevance_status": "relevant",
                        "support_status": "supported",
                    },
                    {
                        "evidence_id": "INC-val/r1/metrics#contra",
                        "relation": "contradicts",
                        "reference_valid": True,
                        "relevance_status": "relevant",
                        "support_status": "contradicted",
                    },
                ],
                "uncertainty": None,
            }
        ],
        "leading_candidate_id": "CAUSE-pay",
        "missing_evidence": [],
        "status": "completed",
        "follow_up_requests": [],
    }


class ValidationTests(unittest.TestCase):
    def test_remediation_requires_known_candidate_and_candidate_linked_evidence(self):
        raw = _base_context()
        raw["root_cause_candidates"][0]["evidence_links"] = raw["root_cause_candidates"][0]["evidence_links"][:2]
        raw["root_cause_candidates"].append(
            {
                "candidate_id": "CAUSE-other",
                "description": "orders-service: unrelated order failures",
                "evidence_links": [
                    {
                        "evidence_id": "INC-val/r1/logs#other",
                        "relation": "supports",
                        "reference_valid": True,
                        "relevance_status": "relevant",
                        "support_status": "supported",
                    }
                ],
                "uncertainty": None,
            }
        )
        context = build_correlation_context(raw)
        options = [
            ProposedOption(
                recommendation_id="REC-no-candidate",
                action="Restart payments-service",
                action_type="remediation",
                target_service="payments-service",
                candidate_id=None,
                expected_impact="Restore service",
                cited_evidence_ids=["INC-val/r1/logs#good"],
                rationale="Has globally supporting evidence but no candidate link",
            ),
            ProposedOption(
                recommendation_id="REC-unknown-candidate",
                action="Restart payments-service",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-unknown",
                expected_impact="Restore service",
                cited_evidence_ids=["INC-val/r1/logs#good"],
                rationale="Candidate does not exist in F3",
            ),
            ProposedOption(
                recommendation_id="REC-foreign-evidence",
                action="Restart payments-service",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Restore service",
                cited_evidence_ids=["INC-val/r1/logs#other"],
                rationale="Evidence belongs to another candidate",
            ),
            ProposedOption(
                recommendation_id="REC-leading-candidate",
                action="Recover payments-service charge handler",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Restore charges",
                cited_evidence_ids=["INC-val/r1/logs#good"],
                rationale="Evidence is linked to the leading candidate",
            ),
        ]

        records = {
            r.recommendation_id: r
            for r in validate_recommendation_evidence(options, context)
        }
        self.assertEqual(records["REC-no-candidate"].validation, "unsupported")
        self.assertEqual(records["REC-unknown-candidate"].validation, "unsupported")
        self.assertEqual(records["REC-foreign-evidence"].validation, "unsupported")
        self.assertEqual(records["REC-leading-candidate"].validation, "supported")

    def test_valid_irrelevant_contradicting_expired_and_missing_citations(self):
        window = IncidentWindow(
            start_time=_ts(14, 0),
            end_time=_ts(14, 10),
            affected_services=["payments-service", "orders-service"],
            dependencies=[("orders-service", "payments-service")],
        )
        # Provide valid supporting refs, an irrelevant-service ref, an out-of-window ref,
        # an expired ref, and omit a missing ref.
        refs = [
            EvidenceRef(
                evidence_id="INC-val/r1/logs#good",
                signal_type="logs",
                service="payments-service",
                source_record_ref="logs#0",
                start_time=_ts(14, 1),
                end_time=_ts(14, 5),
                availability_status="available",
            ),
            EvidenceRef(
                evidence_id="INC-val/r1/metrics#good",
                signal_type="metrics",
                service="payments-service",
                query_ref="prom#1",
                start_time=_ts(14, 1),
                end_time=_ts(14, 5),
                availability_status="available",
            ),
            EvidenceRef(
                evidence_id="INC-val/r1/metrics#contra",
                signal_type="metrics",
                service="payments-service",
                query_ref="prom#contra",
                start_time=_ts(14, 1),
                end_time=_ts(14, 5),
                availability_status="available",
            ),
            EvidenceRef(
                evidence_id="INC-val/r1/logs#wrong-service",
                signal_type="logs",
                service="inventory-service",
                source_record_ref="logs#99",
                start_time=_ts(14, 2),
                end_time=_ts(14, 4),
                availability_status="available",
            ),
            EvidenceRef(
                evidence_id="INC-val/r1/logs#old-time",
                signal_type="logs",
                service="payments-service",
                source_record_ref="logs#old",
                start_time=_ts(11, 0),
                end_time=_ts(11, 5),
                availability_status="available",
            ),
            EvidenceRef(
                evidence_id="INC-val/r1/logs#expired",
                signal_type="logs",
                service="payments-service",
                source_record_ref=None,
                query_ref=None,
                start_time=_ts(14, 1),
                end_time=_ts(14, 5),
                availability_status="expired",
            ),
        ]
        resolver = CatalogEvidenceResolver(refs)
        # Context without candidate-level contradiction for clean baseline test
        clean_raw = _base_context()
        clean_raw["root_cause_candidates"][0]["evidence_links"] = clean_raw["root_cause_candidates"][0]["evidence_links"][:2]
        clean_ctx = build_correlation_context(clean_raw, window=window)

        options = [
            ProposedOption(
                recommendation_id="REC-supported",
                action="Recover payments-service charge handler",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Restore charges",
                cited_evidence_ids=["INC-val/r1/logs#good", "INC-val/r1/metrics#good"],
                rationale="Clean supporting logs and metrics",
            ),
            ProposedOption(
                recommendation_id="REC-wrong-svc",
                action="Scale inventory-service pool",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Irrelevant citation",
                cited_evidence_ids=["INC-val/r1/logs#wrong-service"],
                rationale="Cites wrong service",
            ),
            ProposedOption(
                recommendation_id="REC-old-time",
                action="Restart payments-service",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Stale citation",
                cited_evidence_ids=["INC-val/r1/logs#old-time"],
                rationale="Cites out-of-window log",
            ),
            ProposedOption(
                recommendation_id="REC-expired",
                action="Failover payments-service",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Expired citation",
                cited_evidence_ids=["INC-val/r1/logs#expired"],
                rationale="Cites expired log",
            ),
            ProposedOption(
                recommendation_id="REC-missing",
                action="Patch payments-service",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Missing citation",
                cited_evidence_ids=["INC-val/r1/logs#does-not-exist"],
                rationale="Cites fabricated ref",
            ),
            ProposedOption(
                recommendation_id="REC-mixed",
                action="Adjust payments timeout with partial broken ref",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Partial support",
                cited_evidence_ids=["INC-val/r1/logs#good", "INC-val/r1/logs#does-not-exist"],
                rationale="Mix of valid and missing ref",
            ),
        ]
        records = {
            r.recommendation_id: r
            for r in validate_recommendation_evidence(options, clean_ctx, resolver=resolver)
        }
        self.assertEqual(records["REC-supported"].validation, "supported")
        self.assertEqual(records["REC-wrong-svc"].validation, "unsupported")
        self.assertEqual(records["REC-wrong-svc"].evidence_links[0].relevance_status, "irrelevant")
        self.assertEqual(records["REC-old-time"].validation, "unsupported")
        self.assertEqual(records["REC-old-time"].evidence_links[0].relevance_status, "irrelevant")
        self.assertEqual(records["REC-expired"].validation, "unsupported")
        self.assertEqual(records["REC-missing"].validation, "unsupported")
        self.assertEqual(records["REC-missing"].evidence_links[0].reference_valid, False)
        self.assertEqual(records["REC-mixed"].validation, "unsupported")

        # Contradicting evidence: direct contradiction only -> unsupported; mixed with support -> conditional
        contra_ctx = build_correlation_context(_base_context(), window=window)
        contra_options = [
            ProposedOption(
                recommendation_id="REC-only-contra",
                action="Disable payments-service retries",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Test contra",
                cited_evidence_ids=["INC-val/r1/metrics#contra"],
                rationale="Directly contradicted",
            ),
            ProposedOption(
                recommendation_id="REC-with-cand-contra",
                action="Recover payments-service",
                action_type="remediation",
                target_service="payments-service",
                candidate_id="CAUSE-pay",
                expected_impact="Restore charges",
                cited_evidence_ids=["INC-val/r1/logs#good"],
                rationale="Candidate has contradicting link",
            ),
        ]
        contra_records = {
            r.recommendation_id: r
            for r in validate_recommendation_evidence(contra_options, contra_ctx, resolver=resolver)
        }
        self.assertEqual(contra_records["REC-only-contra"].validation, "unsupported")
        self.assertEqual(contra_records["REC-only-contra"].evidence_links[0].support_status, "contradicted")
        self.assertEqual(contra_records["REC-with-cand-contra"].validation, "conditional")
        self.assertTrue(any("contradicting" in reason.lower() for reason in contra_records["REC-with-cand-contra"].reasons))

    def test_f1_bundle_evidence_resolver_and_live_follow_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports = root / "reports"
            config = settings()
            engine = Engine(root / "data", config)
            try:
                for t in (1000, 1010, 1020):
                    incidents = engine.tick(snapshot(config, t, "payments"))
                doc, = export(incidents, reports)
            finally:
                engine.close()

            iid = doc["id"]
            sid = doc["snapshot_id"]
            follow_up_dir = root / "follow-up-evidence"
            follow_up_dir.mkdir(parents=True)
            (follow_up_dir / "live-abc123.json").write_text(
                json.dumps(
                    {
                        "snapshot_id": sid,
                        "service": "payments-service",
                        "query": "{service_name=\"payments-service\"}",
                        "query_window": {"start": "1970-01-01T00:14:40Z", "end": "1970-01-01T00:17:00Z"},
                    }
                ),
                encoding="utf-8",
            )

            eids = [f"{iid}/{sid}/logs#P1", f"{iid}/{sid}/metrics#M1", f"{iid}/{sid}/logs#live-abc123"]
            resolver = F1ReportEvidenceResolver(
                reports,
                iid,
                follow_up_dir=follow_up_dir,
                fallback_services=["payments-service"],
                service_by_evidence_id={e: "payments-service" for e in eids},
            )
            corr = {
                "incident_id": iid,
                "run_id": "RUN-f1",
                "root_cause_candidates": [
                    {
                        "candidate_id": "CAUSE-f1",
                        "description": "payments-service: payment_failures",
                        "evidence_links": [
                            {
                                "evidence_id": e,
                                "relation": "supports",
                                "reference_valid": True,
                                "relevance_status": "relevant",
                                "support_status": "supported",
                            }
                            for e in eids
                        ],
                        "uncertainty": None,
                    }
                ],
                "leading_candidate_id": "CAUSE-f1",
                "missing_evidence": [],
                "status": "completed",
                "follow_up_requests": [],
            }
            res, state = recommend_with_state(corr, resolver=resolver, use_llm=False)
            self.assertEqual(res.status, "completed")
            self.assertTrue(all(o.validation == "supported" for o in res.recommendation_options))
            # Unresolvable fake ref on real bundle is rejected cleanly
            bad = resolver.resolve(f"{iid}/{sid}/logs#999999")
            self.assertIsNone(bad)


if __name__ == "__main__":
    unittest.main()
