"""Tests for F4-US-03: Ranking, tie-breaking, F4->F7 contract handoff, CLI, and worker integration."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from recommendation_agent import (
    CatalogEvidenceResolver,
    EvidenceRef,
    ProposedOption,
    RecommendationResult,
    ValidationRecord,
    build_correlation_context,
    rank_recommendation,
    recommend,
    recommend_with_state,
    render_markdown,
)


def _ts(minute: int = 0) -> datetime:
    return datetime(2026, 11, 25, 14, minute, 0, tzinfo=timezone.utc)


class RankingAndContractTests(unittest.TestCase):
    def test_ranking_excludes_unsupported_orders_deterministically_and_handles_ties(self):
        corr = {
            "incident_id": "INC-rank",
            "run_id": "RUN-rank",
            "root_cause_candidates": [
                {
                    "candidate_id": "CAUSE-lead",
                    "description": "orders-service: pool saturation",
                    "evidence_links": [
                        {
                            "evidence_id": "INC-rank/r1/logs#1",
                            "relation": "supports",
                            "reference_valid": True,
                            "relevance_status": "relevant",
                            "support_status": "supported",
                        },
                        {
                            "evidence_id": "INC-rank/r1/metrics#1",
                            "relation": "supports",
                            "reference_valid": True,
                            "relevance_status": "relevant",
                            "support_status": "supported",
                        },
                    ],
                    "uncertainty": None,
                }
            ],
            "leading_candidate_id": "CAUSE-lead",
            "missing_evidence": [],
            "status": "completed",
            "follow_up_requests": [],
        }
        ctx = build_correlation_context(corr)
        options = [
            ProposedOption(
                recommendation_id="REC-bad",
                action="Unsupported remediation",
                action_type="remediation",
                target_service="orders-service",
                candidate_id="CAUSE-lead",
                expected_impact="None",
                urgency="critical",
                risk_level="low",
                cited_evidence_ids=[],
                rationale="Bad",
            ),
            ProposedOption(
                recommendation_id="REC-cond",
                action="Conditional remediation",
                action_type="remediation",
                target_service="orders-service",
                candidate_id="CAUSE-lead",
                expected_impact="Partial",
                urgency="high",
                risk_level="medium",
                cited_evidence_ids=["INC-rank/r1/logs#1"],
                rationale="Conditional",
            ),
            ProposedOption(
                recommendation_id="REC-tie-b",
                action="Tie action B",
                action_type="verification",
                target_service="orders-service",
                candidate_id="CAUSE-lead",
                expected_impact="Verify",
                urgency="medium",
                risk_level="low",
                cited_evidence_ids=["INC-rank/r1/logs#1"],
                rationale="Tie B",
            ),
            ProposedOption(
                recommendation_id="REC-tie-a",
                action="Tie action A",
                action_type="verification",
                target_service="orders-service",
                candidate_id="CAUSE-lead",
                expected_impact="Verify",
                urgency="medium",
                risk_level="low",
                cited_evidence_ids=["INC-rank/r1/logs#1"],
                rationale="Tie A",
            ),
            ProposedOption(
                recommendation_id="REC-top",
                action="Top supported remediation",
                action_type="remediation",
                target_service="orders-service",
                candidate_id="CAUSE-lead",
                expected_impact="Fix pool",
                urgency="high",
                risk_level="low",
                cited_evidence_ids=["INC-rank/r1/logs#1", "INC-rank/r1/metrics#1"],
                rationale="Multi-signal supported",
            ),
        ]
        validations = [
            ValidationRecord(recommendation_id="REC-bad", action_type="remediation", validation="unsupported", support_strength=0.0, reasons=["No evidence"]),
            ValidationRecord(recommendation_id="REC-cond", action_type="remediation", validation="conditional", support_strength=0.5, reasons=["Partial"]),
            ValidationRecord(recommendation_id="REC-tie-b", action_type="verification", validation="supported", support_strength=1.0, reasons=["OK"]),
            ValidationRecord(recommendation_id="REC-tie-a", action_type="verification", validation="supported", support_strength=1.0, reasons=["OK"]),
            ValidationRecord(recommendation_id="REC-top", action_type="remediation", validation="supported", support_strength=1.0, reasons=["OK"]),
        ]
        ranked_ids, top_id, details, status = rank_recommendation(options, validations, ctx)
        self.assertEqual(status, "completed")
        self.assertNotIn("REC-bad", ranked_ids)
        self.assertEqual(top_id, "REC-top")
        self.assertEqual(ranked_ids, ["REC-top", "REC-tie-a", "REC-tie-b", "REC-cond"])
        self.assertEqual([d.rank for d in details], [1, 2, 3, 4])
        self.assertTrue(all("score=" in d.explanation for d in details))

    def test_f7_contract_shape_and_failed_f3_propagation(self):
        refs = [
            EvidenceRef(
                evidence_id="INC-f7/r1/logs#1",
                signal_type="logs",
                service="orders-service",
                source_record_ref="logs#1",
                start_time=_ts(1),
                end_time=_ts(4),
                availability_status="available",
            ),
            EvidenceRef(
                evidence_id="INC-f7/r1/metrics#1",
                signal_type="metrics",
                service="orders-service",
                query_ref="prom#1",
                start_time=_ts(1),
                end_time=_ts(4),
                availability_status="available",
            ),
        ]
        corr = {
            "incident_id": "INC-f7",
            "run_id": "RUN-f7",
            "root_cause_candidates": [
                {
                    "candidate_id": "CAUSE-1",
                    "description": "orders-service: release_changed regression",
                    "evidence_links": [
                        {
                            "evidence_id": "INC-f7/r1/logs#1",
                            "relation": "supports",
                            "reference_valid": True,
                            "relevance_status": "relevant",
                            "support_status": "supported",
                        },
                        {
                            "evidence_id": "INC-f7/r1/metrics#1",
                            "relation": "supports",
                            "reference_valid": True,
                            "relevance_status": "relevant",
                            "support_status": "supported",
                        },
                    ],
                    "uncertainty": None,
                }
            ],
            "leading_candidate_id": "CAUSE-1",
            "missing_evidence": [],
            "status": "completed",
            "follow_up_requests": [],
        }
        result, state = recommend_with_state(corr, resolver=CatalogEvidenceResolver(refs), use_llm=False)
        dumped = result.model_dump()
        self.assertEqual(
            set(dumped.keys()),
            {
                "incident_id",
                "correlation_run_id",
                "recommendation_options",
                "ranked_recommendation_ids",
                "top_recommendation_id",
                "status",
            },
        )
        # Ensure F4 does NOT emit final reports or HITL approval flags
        self.assertNotIn("final_report", dumped)
        self.assertNotIn("review_status", dumped)
        for opt in dumped["recommendation_options"]:
            self.assertEqual(
                set(opt.keys()),
                {
                    "recommendation_id",
                    "action",
                    "action_type",
                    "evidence_links",
                    "risk_or_caveat",
                    "validation",
                    "rationale",
                },
            )
        round_trip = RecommendationResult.model_validate_json(result.model_dump_json())
        self.assertEqual(round_trip, result)
        md = render_markdown(result, state)
        self.assertIn("INC-f7", md)
        self.assertIn("Ranked Recommendations", md)

        # Failed F3 input yields typed failed RecommendationResult
        failed_f3 = {
            "incident_id": "INC-fail",
            "run_id": "RUN-fail",
            "root_cause_candidates": [],
            "leading_candidate_id": None,
            "missing_evidence": [],
            "status": "failed",
            "follow_up_requests": [],
            "error": {
                "code": "INVESTIGATION_FAILED",
                "message": "Groq error",
                "retryable": True,
                "operation_id": "RUN-fail",
            },
        }
        failed_res = recommend(failed_f3, use_llm=False)
        self.assertEqual(failed_res.status, "failed")
        self.assertEqual(failed_res.ranked_recommendation_ids, [])
        self.assertIsNone(failed_res.top_recommendation_id)
        self.assertIsNotNone(failed_res.error)

    def test_cli_offline_round_trip(self):
        repo_root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            f3_path = tmp_dir / "f3_output.json"
            out_json = tmp_dir / "f4_output.json"
            out_md = tmp_dir / "f4_output.md"
            f3_path.write_text(
                json.dumps(
                    {
                        "incident_id": "INC-cli",
                        "run_id": "RUN-cli",
                        "root_cause_candidates": [
                            {
                                "candidate_id": "CAUSE-cli",
                                "description": "payments-service: payment_failures spike",
                                "evidence_links": [
                                    {
                                        "evidence_id": "INC-cli/r1/logs#1",
                                        "relation": "supports",
                                        "reference_valid": True,
                                        "relevance_status": "relevant",
                                        "support_status": "supported",
                                    }
                                ],
                                "uncertainty": None,
                            }
                        ],
                        "leading_candidate_id": "CAUSE-cli",
                        "missing_evidence": [],
                        "status": "completed",
                        "follow_up_requests": [],
                    }
                ),
                encoding="utf-8",
            )
            proc = subprocess.run(
                [sys.executable, "-m", "recommendation_agent", str(f3_path), "--no-llm", "--out", str(out_json)],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            payload = RecommendationResult.model_validate_json(proc.stdout)
            self.assertEqual(payload.incident_id, "INC-cli")
            self.assertTrue(out_json.is_file())

            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "recommendation_agent",
                    str(out_json),
                    "--from-result",
                    "--out",
                    str(out_md),
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("INC-cli", out_md.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
