import copy
import json
import re
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from observa.f5_memory import IncidentMemory, MemoryOperationError
from observa.f5_memory.relevance import KeywordScorer, tokenize
from observa.f5_memory.service import DEFAULT_DATABASE, SCHEMA_VERSION
from observa.f5_memory.sources import FakeApprovalSource
from observa.f5_memory.validation import content_digest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
APPROVED_LATEST = ["DEC-201-v2", "DEC-202-v1", "DEC-203-v1", "DEC-204-v1", "DEC-205-v1", "DEC-207-v1", "DEC-208-v1"]


def rehash(snapshot):
    digest = content_digest(snapshot["report"]["content"])
    snapshot["report"].update(content_digest=digest, content_ref=f"sha256:{digest}")


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "memory.sqlite3"
        self.snapshots = json.loads((FIXTURES / "approval_snapshots.json").read_text(encoding="utf-8"))
        self.by_id = {s["decision"]["decision_id"]: s for s in self.snapshots}
        self.current = json.loads((FIXTURES / "current_incidents.json").read_text())
        self.source = FakeApprovalSource(self.snapshots)
        self.memory = IncidentMemory(self.path, self.source)

    def save(self, decision_id="DEC-201-v1", memory=None):
        return (memory or self.memory).save_approved_incident(decision_id)

    def save_latest(self):
        for decision_id in APPROVED_LATEST:
            self.save(decision_id)

    def changed_source(self, mutate, decision_id="DEC-201-v1", rehash_content=False):
        snapshot = copy.deepcopy(self.by_id[decision_id])
        mutate(snapshot)
        if rehash_content:
            rehash(snapshot)
        return IncidentMemory(self.path, FakeApprovalSource([snapshot]))

    def assert_error(self, code, fn):
        with self.assertRaises(MemoryOperationError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def ids(self, result):
        return [item["source_incident_id"] for item in result["items"]]

    def check(self, name, limit=5):
        incident = {k: v for k, v in self.current[name].items() if k != "findings"}
        return self.memory.check_memory(incident, self.current[name]["findings"], limit)


class SaveTests(MemoryTestCase):
    def test_persistence_and_provenance_after_restart(self):
        saved = self.save()["item"]
        reopened = IncidentMemory(self.path)
        self.assertEqual(reopened.get_approved_knowledge(saved["knowledge_id"]), saved)
        self.assertEqual(saved["approval_decision_id"], "DEC-201-v1")
        self.assertEqual(saved["approved_report_id"], "RPT-201")
        self.assertEqual(saved["incident_context"]["alerts"], ["ALERT-201"])
        self.assertEqual(saved["report_content_ref"], self.by_id["DEC-201-v1"]["report"]["content_ref"])
        self.assertEqual(saved["reviewer_id"], "SIMULATED-REVIEWER")

    def test_new_format_has_its_own_default_database(self):
        self.assertEqual(SCHEMA_VERSION, 3)
        self.assertEqual(DEFAULT_DATABASE, "data/incident-memory-v3.sqlite3")

    def test_final_root_cause_is_kept_separate_from_verified(self):
        saved = self.save("DEC-201-v2")["item"]
        self.assertIn("connection-pool exhaustion", saved["final_root_cause"]["description"])
        self.assertEqual(saved["final_root_cause"]["candidate_id"], "CAND-201-2")
        self.assertNotIn("confidence", saved["final_root_cause"])
        self.assertIsNone(saved["verified_root_cause"])
        self.assertEqual(saved["root_cause_status"], "leading")
        self.assertEqual([c["candidate_id"] for c in saved["alternative_root_causes"]], ["CAND-201-1"])

    def test_inconclusive_record_keeps_unknowns_explicit(self):
        saved = self.save("DEC-208-v1")["item"]
        self.assertIsNone(saved["final_root_cause"])
        self.assertEqual(saved["root_cause_status"], "inconclusive")
        self.assertEqual(saved["accepted_recommendations"], [])
        self.assertEqual(saved["recommendation_status"], "no_validated_remediation")

    def test_recommendation_approval_separate_from_outcome(self):
        for decision_id in ("DEC-201-v2", "DEC-203-v1"):
            rec = self.save(decision_id)["item"]["accepted_recommendations"][0]
            self.assertEqual((rec["execution_status"], rec["resolution_status"]), ("unknown", "unknown"))
            self.assertIn("rationale", rec)
        stored = self.memory.list_approved_versions("INC-203")[0]
        content = self.by_id["DEC-203-v1"]["report"]["content"]
        self.assertNotIn("execution_status", content["recommendations"]["ranked"][0])
        self.assertEqual(stored["other_recommendation_options"], [])

    def test_approval_carries_no_reviewer_comment(self):
        """F6 stores null feedback on approve; only change requests keep the reviewer's words."""
        self.assertIsNone(self.save("DEC-205-v1")["item"]["reviewer_comment"])

    def test_findings_are_stored_structured(self):
        findings = self.save("DEC-202-v1")["item"]["findings"]
        self.assertEqual([f["signal_type"] for f in findings], ["logs", "metrics", "metrics", "traces"])
        first = findings[0]
        self.assertEqual(first["finding_id"], "INC-202:logs:F1")
        self.assertEqual(first["claim_kind"], "observation")
        self.assertEqual(first["evidence_links"][0]["relation"], "supports")

    def test_missing_evidence_and_open_window_stay_explicit(self):
        saved = self.save("DEC-204-v1")["item"]
        self.assertIsNone(saved["incident_context"]["end_time"])
        self.assertEqual([m["requested_from"] for m in saved["missing_evidence"]], ["logs", "traces"])
        self.assertIn("missing_evidence", saved["uncertainty_flags"])
        self.assertEqual(saved["signal_status"]["logs"], "partial")

    def test_duplicate_delivery_is_idempotent(self):
        first, second = self.save(), self.save()
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["item"], second["item"])
        self.assertEqual(len(self.memory.list_approved_versions("INC-201")), 1)

    def test_concurrent_duplicate_delivery(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.save(), range(4)))
        self.assertEqual(sum(r["created"] for r in results), 1)

    def test_rejected_and_pending_reports_never_enter_memory(self):
        self.assert_error("NOT_APPROVED", lambda: self.save("DEC-206-v1"))
        self.assert_error("NOT_FOUND", lambda: self.save("NO-DECISION-FOR-PENDING"))
        self.assertEqual(self.memory.search_approved_incidents("PostgreSQL outage")["items"], [])

    def test_writes_without_approval_source_are_refused(self):
        self.assert_error("NOT_AUTHORIZED", lambda: IncidentMemory(self.path).save_approved_incident("anything"))

    def test_exact_report_version_required(self):
        other = self.changed_source(lambda s: s["decision"].update(version=2))
        self.assert_error("VERSION_CONFLICT", lambda: self.save(memory=other))

    def test_report_content_digest_is_checked(self):
        other = self.changed_source(lambda s: s["report"]["content"]["overview"].update(title="tampered"))
        self.assert_error("VERSION_CONFLICT", lambda: self.save(memory=other))

    def test_changed_payload_cannot_reuse_approval_identity(self):
        self.save()
        other = self.changed_source(lambda s: s["report"]["content"]["overview"].update(title="different"),
                                    rehash_content=True)
        self.assert_error("VERSION_CONFLICT", lambda: self.save(memory=other))

    def test_idempotency_key_cannot_be_reused_for_another_incident(self):
        self.save()
        other = self.changed_source(lambda s: s["decision"].update(idempotency_key="approval-201-v1"), "DEC-202-v1")
        self.assert_error("VERSION_CONFLICT", lambda: self.save("DEC-202-v1", memory=other))

    def test_failed_save_can_be_retried_without_false_success(self):
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TRIGGER fail_write BEFORE INSERT ON knowledge BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        self.assert_error("STORAGE_FAILURE", self.save)
        self.assertEqual(self.memory.list_approved_versions("INC-201"), [])
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TRIGGER fail_write")
        self.assertTrue(self.save()["created"])
        self.assertFalse(self.save()["created"])

    def test_validation_rejects_malformed_inputs(self):
        content = lambda s: s["report"]["content"]  # noqa: E731
        mutations = [
            lambda s: content(s)["overview"].update(start_time="2026-01-01T00:00:00"),
            lambda s: content(s)["overview"].update(end_time="2020-01-01T00:00:00Z"),
            lambda s: content(s)["overview"].update(affected_services="orders-service"),
            lambda s: content(s)["overview"].update(severity="warning"),
            lambda s: s["report"].update(version=True),
            lambda s: s["report"].update(previous_version=1),
            lambda s: s["report"].pop("evidence_ids"),
            lambda s: s["decision"].update(memory_sync_status="stored"),
            lambda s: s["decision"].update(feedback="   "),
            lambda s: content(s)["findings"]["logs"]["findings"][0].update(claim_kind="guess"),
            lambda s: content(s)["findings"]["logs"]["findings"][0]["evidence_links"][0].update(reference_valid=1),
            lambda s: content(s)["root_cause"].update(leading=None),
            lambda s: content(s)["recommendations"]["ranked"][0].update(rank=0),
            lambda s: content(s)["recommendations"]["ranked"][0].update(validation="approved"),
            lambda s: content(s).pop("executive_summary"),
            lambda s: content(s)["missing_evidence"].append({"source": "F4", "required": "x", "reason": "y",
                                                             "requested_from": "logs", "status": "unavailable"}),
            lambda s: s.update(synthetic=False),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                other = self.changed_source(mutate, rehash_content=True)
                self.assert_error("VALIDATION_ERROR", lambda: self.save(memory=other))

    def test_content_ref_must_name_the_digest(self):
        other = self.changed_source(lambda s: s["report"].update(content_ref="sha256:" + "0" * 64))
        self.assert_error("VALIDATION_ERROR", lambda: self.save(memory=other))

    def test_unknown_report_schema_is_reported(self):
        other = self.changed_source(lambda s: s["report"]["content"].update(schema_version="2.0"), rehash_content=True)
        self.assert_error("SCHEMA_MISMATCH", lambda: self.save(memory=other))

    def test_oversized_version_is_validation_error(self):
        def mutate(snapshot):
            snapshot["report"]["version"] = 2**63
            snapshot["decision"]["version"] = 2**63
        other = self.changed_source(mutate)
        self.assert_error("VALIDATION_ERROR", lambda: self.save(memory=other))

    def test_old_format_database_gives_clear_error(self):
        old = Path(self.directory.name) / "old.sqlite3"
        with sqlite3.connect(old) as db:
            db.execute("CREATE TABLE knowledge (knowledge_id TEXT PRIMARY KEY)")
        self.assert_error("SCHEMA_MISMATCH", lambda: IncidentMemory(old))
        with sqlite3.connect(old) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0], 0)

    def test_format_2_database_is_left_untouched(self):
        old = Path(self.directory.name) / "v2.sqlite3"
        with sqlite3.connect(old) as db:
            db.execute("CREATE TABLE knowledge (knowledge_id TEXT PRIMARY KEY, snapshot TEXT)")
            db.execute("INSERT INTO knowledge VALUES ('IKM-old', '{}')")
            db.execute("PRAGMA user_version = 2")
        self.assert_error("SCHEMA_MISMATCH", lambda: IncidentMemory(old))
        with sqlite3.connect(old) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT knowledge_id FROM knowledge").fetchall(), [("IKM-old",)])


class RevisionTests(MemoryTestCase):
    def test_latest_approved_revision_only_in_search_history_preserved(self):
        old = self.save()["item"]
        self.save("DEC-201-v2")
        items = self.memory.search_approved_incidents("acquire spans")["items"]
        self.assertEqual([i["approved_version"] for i in items], [2])
        self.assertFalse(self.memory.get_approved_knowledge(old["knowledge_id"])["is_current_approved_version"])
        self.assertEqual(len(self.memory.list_approved_versions("INC-201")), 2)
        self.assertFalse(self.save()["created"])

    def test_correction_replaces_old_root_cause_in_search(self):
        self.save()
        item = self.memory.search_approved_incidents("PostgreSQL server limit")["items"][0]
        self.assertEqual(item["final_root_cause"]["candidate_id"], "CAND-201-1")
        self.save("DEC-201-v2")
        item = self.memory.search_approved_incidents("connection pool exhaustion")["items"][0]
        self.assertEqual(item["final_root_cause"]["candidate_id"], "CAND-201-2")
        self.assertEqual(item["approved_version"], 2)

    def test_out_of_order_trusted_delivery_does_not_downgrade_current(self):
        self.save("DEC-201-v2")
        self.save()
        self.assertEqual(self.memory.search_approved_incidents("pool")["items"][0]["approved_version"], 2)

    def test_rejected_revision_does_not_replace_approved_version(self):
        self.save("DEC-203-v1")
        self.assert_error("NOT_APPROVED", lambda: self.save("DEC-203-v2"))
        item = self.memory.search_approved_incidents("release")["items"][0]
        self.assertEqual(item["approved_version"], 1)
        self.assertIn("v2-buggy", item["final_root_cause"]["description"])


class SearchTests(MemoryTestCase):
    def test_tokenizer_splits_telemetry_names_and_plurals(self):
        self.assertLessEqual({"db", "pool", "timeout"}, tokenize("db_pool_timeout"))
        self.assertLessEqual({"db", "pool", "acquire"}, tokenize("db.pool.acquire.duration"))
        self.assertLessEqual({"persistence", "compatibility"}, tokenize("OrderPersistenceCompatibilityError"))
        self.assertEqual(tokenize("timeouts"), tokenize("timeout"))
        self.assertEqual(tokenize("the errors"), set())

    def test_telemetry_terms_and_root_cause_are_searchable(self):
        self.save_latest()
        self.assertEqual(self.ids(self.memory.search_approved_incidents("timeouts"))[0], "INC-201")
        result = self.memory.search_approved_incidents("connection-pool exhaustion")
        self.assertEqual(self.ids(result)[0], "INC-201")
        self.assertIn("exhaustion", result["items"][0]["relevance_basis"]["matched_in"]["root_cause"])

    def test_indicators_are_searchable(self):
        self.save_latest()
        result = self.memory.search_approved_incidents("payment_unavailable")
        self.assertEqual(self.ids(result)[0], "INC-202")

    def test_generated_summary_wording_is_not_searched(self):
        self.save_latest()
        self.assertEqual(self.memory.search_approved_incidents("probable verified human ranked")["items"], [])

    def test_shared_service_alone_is_not_a_match(self):
        self.save_latest()
        self.assertEqual(self.memory.search_approved_incidents("unrelated zebra", ["api-gateway"])["items"], [])
        self.assertEqual(self.memory.search_approved_incidents("", ["orders-service"])["items"], [])

    def test_monitoring_pseudo_services_never_count_as_shared(self):
        record = {"incident_context": {"title": "x", "affected_services": ["observability:prometheus", "orders-service"]},
                  "findings": [], "final_root_cause": None, "verified_root_cause": None, "accepted_recommendations": []}
        scorer = KeywordScorer(min_matched_terms=0)
        self.assertIsNone(scorer.score("", ["observability:prometheus"], record))
        basis = scorer.score("", ["observability:coverage", "orders"], record)
        self.assertEqual(basis["matched_services"], ["orders"])
        self.assertEqual(basis["current_only_services"], [])

    def test_rules_are_configurable(self):
        self.save_latest()
        memory = IncidentMemory(self.path, scorer=KeywordScorer(min_matched_terms=0))
        self.assertEqual(len(memory.search_approved_incidents("", ["api-gateway"])["items"]), 3)
        weighted = IncidentMemory(self.path, scorer=KeywordScorer(service_weight=50))
        item = weighted.search_approved_incidents("pool", ["orders-service"])["items"][0]
        self.assertGreaterEqual(item["relevance_basis"]["ranking_score"], 50)

    def test_ranking_explains_services_terms_and_differences(self):
        self.save_latest()
        first = self.memory.search_approved_incidents("db_pool_timeout", ["orders", "api-gateway", "cart"])["items"][0]
        basis = first["relevance_basis"]
        self.assertEqual(first["source_incident_id"], "INC-201")
        self.assertEqual(basis["matched_services"], ["api-gateway", "orders"])
        self.assertEqual(basis["current_only_services"], ["cart"])
        self.assertIn("timeout", basis["matched_in"]["findings"])
        self.assertEqual(first["knowledge_origin"], "historical")

    def test_each_current_incident_finds_the_right_history_first(self):
        self.save_latest()
        self.assertEqual(self.ids(self.check("pool"))[0], "INC-201")
        self.assertEqual(self.ids(self.check("payments"))[0], "INC-202")
        self.assertEqual(self.ids(self.check("release"))[0], "INC-203")

    def test_irrelevant_current_incident_finds_nothing(self):
        self.save_latest()
        self.assertEqual(self.check("unrelated")["items"], [])

    def test_structured_findings_are_accepted_by_check_memory(self):
        self.save_latest()
        incident = {k: v for k, v in self.current["pool"].items() if k != "findings"}
        findings = [{"finding_id": "INC-CURRENT-POOL:logs:F1", "claim": "Pool acquisition timed out",
                     "indicator": "db_pool_timeout", "service": "orders-service"}]
        self.assertEqual(self.ids(self.memory.check_memory(incident, findings))[0], "INC-201")
        self.assert_error("VALIDATION_ERROR", lambda: self.memory.check_memory(incident, [{"indicator": "x"}]))

    def test_same_symptoms_different_causes_stay_historical(self):
        """IKM-04: identical titles must not make either past cause the current answer."""
        self.save_latest()
        title = lambda d: self.by_id[d]["report"]["content"]["overview"]["title"]  # noqa: E731
        self.assertEqual(title("DEC-201-v2"), title("DEC-203-v1"))
        result = self.check("release")
        by_id = {i["source_incident_id"]: i for i in result["items"]}
        self.assertGreater(by_id["INC-203"]["relevance_basis"]["ranking_score"],
                           by_id["INC-201"]["relevance_basis"]["ranking_score"])
        self.assertNotIn("final_root_cause", result)
        for item in result["items"]:
            self.assertEqual(item["knowledge_origin"], "historical")
            self.assertIn("not confirmation", item["relevance_basis"]["caveat"])

    def test_check_memory_does_not_mutate_context_and_excludes_own_incident(self):
        self.save_latest()
        incident = copy.deepcopy(self.by_id["DEC-201-v2"]["report"]["content"]["overview"])
        before = copy.deepcopy(incident)
        result = self.memory.check_memory(incident, ["db_pool_timeout db.pool.acquire"])
        self.assertEqual(incident, before)
        self.assertNotIn("INC-201", self.ids(result))

    def test_empty_search_no_match_and_failure_are_distinct(self):
        self.assertEqual(self.memory.search_approved_incidents("timeout"), {"status": "ok", "items": [], "error": None})
        self.save()
        self.assertEqual(self.memory.search_approved_incidents("unrelated-zebra")["items"], [])
        self.assertEqual(self.memory.search_approved_incidents()["items"], [])
        self.path.unlink()
        result = self.memory.search_approved_incidents("timeout")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"]["code"], "STORAGE_FAILURE")
        self.assertTrue(result["error"]["retryable"])

    def test_queries_validate_types_and_bounds(self):
        cases = ({"limit": True}, {"limit": 0}, {"limit": 101}, {"affected_services": "orders"}, {"query": None},
                 {"min_matched_terms": -1})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                self.assert_error("VALIDATION_ERROR", lambda: self.memory.search_approved_incidents(**kwargs))

    def test_corrupt_stored_record_fails_without_returning_history(self):
        saved = self.save()["item"]
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE knowledge SET snapshot=?", ("{invalid",))
        result = self.memory.search_approved_incidents("pool")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["items"], [])
        self.assert_error("STORAGE_FAILURE", lambda: self.memory.get_approved_knowledge(saved["knowledge_id"]))

    def test_sql_text_is_handled_as_data(self):
        self.save()
        self.assertIsNone(self.memory.get_approved_knowledge("' OR 1=1; --"))
        self.assertEqual(self.memory.list_approved_versions("'; DROP TABLE knowledge; --"), [])
        self.assertEqual(len(self.memory.list_approved_versions("INC-201")), 1)

    def test_inspection_is_read_only_and_unknown_id_returns_none(self):
        saved = self.save()["item"]
        self.assertIsNone(self.memory.get_approved_knowledge("unknown"))
        self.memory.get_approved_knowledge(saved["knowledge_id"])
        self.assertFalse(self.save()["created"])


class FixtureTests(MemoryTestCase):
    def test_unresolved_evidence_remains_explicit(self):
        statuses = [r["reference_status"] for r in self.save("DEC-204-v1")["item"]["evidence_refs"]]
        self.assertEqual(statuses, ["unresolved", "unresolved"])
        statuses = [r["reference_status"] for r in self.save("DEC-207-v1")["item"]["evidence_refs"]]
        self.assertEqual(statuses, ["valid", "unresolved"])

    def test_valid_references_have_synthetic_sample_records(self):
        evidence = json.loads((FIXTURES / "evidence_records.json").read_text())
        for snapshot in self.snapshots:
            for ref in snapshot["report"]["content"]["evidence_register"]:
                if ref["reference_status"] == "valid":
                    self.assertTrue(evidence[ref["evidence_id"]]["synthetic"])

    def test_all_three_signals_are_represented(self):
        signals = {r["signal_type"] for s in self.snapshots for r in s["report"]["content"]["evidence_register"]}
        self.assertEqual(signals, {"logs", "metrics", "traces"})

    def test_fixture_digests_match_their_content(self):
        for snapshot in self.snapshots:
            report = snapshot["report"]
            self.assertEqual(report["content_digest"], content_digest(report["content"]))
            self.assertEqual(report["content_ref"], "sha256:" + report["content_digest"])

    def test_all_fixture_data_is_labelled_and_reviewer_is_simulated(self):
        for snapshot in self.snapshots:
            self.assertTrue(snapshot["synthetic"])
            self.assertEqual(snapshot["decision"]["reviewer_id"], "SIMULATED-REVIEWER")

    def test_fixtures_do_not_contain_dummy_app_fault_labels(self):
        """OBSERVA must not see the dummy app's ground-truth scenario names."""
        labels = re.compile(r"db[ _]connection[ _]exhaustion|db[ _]exhaustion|payments[ _]errors|bad[ _]deployment"
                            r"|fault_injected", re.I)
        names = sorted(path.name for path in FIXTURES.glob("*.json"))
        self.assertIn("f1_incident_sample.json", names)
        for name in names:
            self.assertIsNone(labels.search((FIXTURES / name).read_text(encoding="utf-8")), name)


if __name__ == "__main__":
    unittest.main()
