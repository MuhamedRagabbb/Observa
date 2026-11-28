"""Tests for F4-US-01: Evidence-aware recommendation option generation."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest

from recommendation_agent import (
    ApprovedKnowledge,
    CatalogEvidenceResolver,
    EvidenceRef,
    LLMProviderError,
    MemorySearchResult,
    ScriptedLLM,
    recommend,
    recommend_with_state,
)


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 25, hour, minute, 0, tzinfo=timezone.utc)


def _ref(eid: str, signal: str, service: str, hour: int = 10) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=eid,
        signal_type=signal,
        service=service,
        source_record_ref=f"/reports/{eid}",
        query_ref=eid,
        start_time=_ts(hour, 1),
        end_time=_ts(hour, 5),
        availability_status="available",
    )


def _link(eid: str, *, relation: str = "supports", support: str = "supported") -> dict:
    return {
        "evidence_id": eid,
        "relation": relation,
        "reference_valid": True,
        "relevance_status": "relevant",
        "support_status": support,
    }


class OptionGenerationTests(unittest.TestCase):
    def test_db_exhaustion_payments_and_bad_deployment_scenarios(self):
        scenarios = [
            (
                "INC-db",
                "orders-service",
                "orders-service: Database connection pool exhausted (pool_timeouts / db_pool_timeout)",
                ["INC-db/r1/logs#db_pool_timeout", "INC-db/r1/metrics#pool_pressure"],
                "pool",
            ),
            (
                "INC-pay",
                "payments-service",
                "payments-service: Elevated POST /payments/charge 503 payment_failures",
                ["INC-pay/r1/logs#payment_failed", "INC-pay/r1/metrics#payment_failures"],
                "charge",
            ),
            (
                "INC-dep",
                "orders-service",
                "orders-service: Regression after release_changed to v2-buggy causing order_persistence_failed",
                ["INC-dep/r1/logs#release_changed", "INC-dep/r1/traces#T1"],
                "roll back",
            ),
        ]
        for iid, svc, desc, eids, expected_kw in scenarios:
            with self.subTest(incident=iid):
                refs = [_ref(eid, eid.split("/")[2].split("#")[0], svc) for eid in eids]
                corr = {
                    "incident_id": iid,
                    "run_id": f"RUN-{iid}",
                    "root_cause_candidates": [
                        {
                            "candidate_id": f"CAUSE-{iid}",
                            "description": desc,
                            "evidence_links": [_link(eid) for eid in eids],
                            "uncertainty": None,
                        }
                    ],
                    "leading_candidate_id": f"CAUSE-{iid}",
                    "missing_evidence": [],
                    "status": "completed",
                    "follow_up_requests": [],
                }
                result, state = recommend_with_state(
                    corr,
                    resolver=CatalogEvidenceResolver(refs),
                    use_llm=False,
                )
                self.assertEqual(result.status, "completed")
                remediations = [o for o in result.recommendation_options if o.action_type == "remediation"]
                verifications = [o for o in result.recommendation_options if o.action_type == "verification"]
                self.assertTrue(remediations)
                self.assertTrue(verifications)
                self.assertIn(expected_kw, remediations[0].action.lower())
                self.assertTrue(all(o.validation == "supported" for o in result.recommendation_options))
                self.assertIsNotNone(result.top_recommendation_id)
                self.assertEqual(state.recommendation_options[0].target_service, svc)

    def test_inconclusive_and_empty_cases_never_invent_remediations(self):
        # Inconclusive with missing evidence -> only investigation/verification, zero remediations
        corr_gap = {
            "incident_id": "INC-inc",
            "run_id": "RUN-inc",
            "root_cause_candidates": [],
            "leading_candidate_id": None,
            "missing_evidence": [
                {
                    "required": "Active and maximum database pool connections for orders-service",
                    "reason": "Pool saturation unverified",
                    "requested_from": "metrics",
                    "status": "unavailable",
                }
            ],
            "status": "inconclusive",
            "follow_up_requests": [],
        }
        result_gap = recommend(corr_gap, use_llm=False)
        self.assertEqual(result_gap.status, "inconclusive")
        self.assertFalse(any(o.action_type == "remediation" for o in result_gap.recommendation_options))
        self.assertTrue(any(o.action_type == "investigation" for o in result_gap.recommendation_options))

        # Normal / empty correlation with no gaps or candidates -> zero fabricated options
        corr_empty = {
            "incident_id": "INC-norm",
            "run_id": "RUN-norm",
            "root_cause_candidates": [],
            "leading_candidate_id": None,
            "missing_evidence": [],
            "status": "inconclusive",
            "follow_up_requests": [],
        }
        result_empty = recommend(corr_empty, use_llm=False)
        self.assertEqual(result_empty.status, "inconclusive")
        self.assertEqual(result_empty.recommendation_options, [])
        self.assertEqual(result_empty.ranked_recommendation_ids, [])
        self.assertIsNone(result_empty.top_recommendation_id)

    def test_memory_empty_vs_failed_vs_approved_and_unapproved_filtered(self):
        eids = ["INC-mem/r1/logs#1", "INC-mem/r1/metrics#1"]
        refs = [_ref("INC-mem/r1/logs#1", "logs", "payments-service"), _ref("INC-mem/r1/metrics#1", "metrics", "payments-service")]
        corr = {
            "incident_id": "INC-mem",
            "run_id": "RUN-mem",
            "root_cause_candidates": [
                {
                    "candidate_id": "CAUSE-1",
                    "description": "payments-service: Elevated payment_failures",
                    "evidence_links": [_link(e) for e in eids],
                    "uncertainty": None,
                }
            ],
            "leading_candidate_id": "CAUSE-1",
            "missing_evidence": [],
            "status": "completed",
            "follow_up_requests": [],
        }

        # Empty memory ok + []
        res_empty, st_empty = recommend_with_state(
            corr,
            resolver=CatalogEvidenceResolver(refs),
            memory_lookup=lambda _i, _s: MemorySearchResult(status="ok", items=[]),
            use_llm=False,
        )
        self.assertEqual(st_empty.memory_status, "ok")
        self.assertEqual(st_empty.memory_items_count, 0)
        self.assertEqual(res_empty.status, "completed")

        # Failed memory retrieval -> recorded warning, current evidence still produces recommendations
        def fail_mem(_i, _s):
            raise RuntimeError("F5 DB timeout")

        res_fail, st_fail = recommend_with_state(
            corr,
            resolver=CatalogEvidenceResolver(refs),
            memory_lookup=fail_mem,
            use_llm=False,
        )
        self.assertEqual(st_fail.memory_status, "failed")
        self.assertTrue(any("memory lookup failed" in w.lower() for w in st_fail.warnings))
        self.assertEqual(res_fail.status, "completed")

        # Approved memory + unapproved draft (must be filtered out by F4 memory adapter)
        approved = ApprovedKnowledge(
            knowledge_id="KNOW-1",
            source_incident_id="INC-old",
            approved_report_id="RPT-old",
            approved_version=2,
            approval_decision_id="DEC-1",
            approved_at=_ts(9),
            findings=[],
            evidence_ids=["INC-old/r1/logs#99"],
            reviewed_assessment="Payments gateway timeout fixed via policy reset.",
            verified_root_cause=None,
            accepted_recommendations=[{"action": "Reset failure policy on payments-service"}],
            record_version=1,
            is_current_approved_version=True,
            affected_services=["payments-service"],
        )
        unapproved = approved.model_copy(update={"knowledge_id": "KNOW-UNAPPROVED", "is_current_approved_version": False})
        res_mem, st_mem = recommend_with_state(
            corr,
            resolver=CatalogEvidenceResolver(refs),
            memory_lookup=lambda _i, _s: [approved, unapproved],
            use_llm=False,
        )
        self.assertEqual(st_mem.memory_items_count, 1)
        rem = next(o for o in st_mem.recommendation_options if o.action_type == "remediation")
        self.assertEqual(rem.historical_reference_ids, ["KNOW-1"])
        # Current evidence links must NOT copy historical evidence IDs
        contract_rem = next(o for o in res_mem.recommendation_options if o.recommendation_id == rem.recommendation_id)
        self.assertNotIn("INC-old/r1/logs#99", {l.evidence_id for l in contract_rem.evidence_links})

    def test_llm_guardrails_block_inconclusive_remediation_and_surface_invalid_json(self):
        corr_inc = {
            "incident_id": "INC-llm-inc",
            "run_id": "RUN-llm-inc",
            "root_cause_candidates": [],
            "leading_candidate_id": None,
            "missing_evidence": [
                {"required": "traces", "reason": "missing", "requested_from": "traces", "status": "unavailable"}
            ],
            "status": "inconclusive",
            "follow_up_requests": [],
        }
        # Even if LLM attempts to inject a remediation on inconclusive RCA, F4 strips it.
        scripted = ScriptedLLM(
            [
                json.dumps(
                    {
                        "options": [
                            {
                                "action": "Restart PostgreSQL immediately",
                                "action_type": "remediation",
                                "target_service": "orders-service",
                                "expected_impact": "Guessed fix",
                                "urgency": "critical",
                                "risk_level": "high",
                                "cited_evidence_ids": [],
                                "rationale": "LLM guess",
                            },
                            {
                                "action": "Collect missing trace spans for orders-service",
                                "action_type": "investigation",
                                "target_service": "orders-service",
                                "expected_impact": "Clarify call path",
                                "urgency": "high",
                                "risk_level": "low",
                                "cited_evidence_ids": [],
                                "rationale": "Missing trace gap",
                            },
                        ]
                    }
                )
            ]
        )
        res = recommend(corr_inc, llm=scripted, use_llm=True)
        self.assertEqual(res.status, "inconclusive")
        self.assertFalse(any(o.action_type == "remediation" for o in res.recommendation_options))
        self.assertEqual(len(res.recommendation_options), 1)
        self.assertEqual(res.recommendation_options[0].action_type, "investigation")

        bad_llm = ScriptedLLM(["not json at all"])
        with self.assertRaises(LLMProviderError):
            recommend(corr_inc, llm=bad_llm, use_llm=True)

    def test_observa_f5_memory_adapter_shape_and_real_store(self):
        import tempfile
        from observa.f5_memory import IncidentMemory
        from recommendation_agent import build_correlation_context, f5_memory_lookup, search_approved_incidents

        ctx = build_correlation_context(
            {
                "incident_id": "INC-f5",
                "run_id": "RUN-f5",
                "root_cause_candidates": [
                    {
                        "candidate_id": "CAUSE-1",
                        "description": "payments-service: gateway failure",
                        "evidence_links": [],
                        "uncertainty": None,
                    }
                ],
                "leading_candidate_id": "CAUSE-1",
                "missing_evidence": [],
                "status": "completed",
                "follow_up_requests": [],
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            from pathlib import Path
            from observa.f5_memory.sources import FakeApprovalSource

            store = IncidentMemory(f"{tmp}/memory.sqlite3")
            res = search_approved_incidents(ctx, f5_memory_lookup(store))
            self.assertEqual(res.status, "ok")
            self.assertEqual(res.items, [])

            snaps_path = Path(__file__).resolve().parents[2] / "fixtures" / "approval_snapshots.json"
            if snaps_path.is_file():
                snaps = json.loads(snaps_path.read_text(encoding="utf-8"))
                seeded = IncidentMemory(f"{tmp}/seeded.sqlite3", approval_source=FakeApprovalSource(snaps))
                for s in snaps:
                    if s["decision"]["decision"] == "approve":
                        seeded.save_approved_incident(s["decision"]["decision_id"])
                seeded_res = search_approved_incidents(ctx, f5_memory_lookup(seeded))
                self.assertEqual(seeded_res.status, "ok")
                self.assertGreaterEqual(len(seeded_res.items), 1)

        # Also verify dict-shaped items returned by observa.f5_memory._record() normalize cleanly.
        dict_payload = {
            "status": "ok",
            "items": [
                {
                    "knowledge_id": "KNOW-F5-1",
                    "source_incident_id": "INC-prev",
                    "approved_report_id": "RPT-prev",
                    "approved_version": 1,
                    "approval_decision_id": "DEC-prev",
                    "approved_at": "2026-11-24T10:00:00Z",
                    "findings": [],
                    "incident_context": {"affected_services": ["payments-service"]},
                    "executive_summary": "Payments gateway timeout fixed by disabling fault policy.",
                    "final_root_cause": {"description": "Faulty payment policy"},
                    "accepted_recommendations": [{"action": "Disable fault policy"}],
                    "record_version": 1,
                    "is_current_approved_version": True,
                    "relevance_basis": {"matched_services": ["payments-service"], "ranking_score": 10},
                }
            ],
            "error": None,
        }
        norm = search_approved_incidents(ctx, lambda _i, _s: dict_payload)
        self.assertEqual(norm.status, "ok")
        self.assertEqual(len(norm.items), 1)
        self.assertEqual(norm.items[0].affected_services, ["payments-service"])
        self.assertEqual(norm.items[0].verified_root_cause, "Faulty payment policy")


if __name__ == "__main__":
    unittest.main()
