"""F6 → F5 write path: IncidentMemorySink behind F6's MemorySink port (fake F6/F7 data only)."""

import copy
import json
import socket
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from observa.f5_memory import IncidentMemory, MemoryOperationError
from observa.f5_memory.f6_handoff import (FakeReviewService, F6ApprovalSource, HttpReviewServiceClient,
                                          IncidentMemorySink, handoff_for, validate_handoff)
from tests.review_server import ReviewServer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class HandoffTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "memory.sqlite3"
        self.snapshots = json.loads((FIXTURES / "approval_snapshots.json").read_text(encoding="utf-8"))
        self.by_id = {s["decision"]["decision_id"]: s for s in self.snapshots}
        self.fake = FakeReviewService(self.snapshots)
        self.sink = IncidentMemorySink.connect(self.path, self.fake)

    def handoff(self, decision_id="DEC-201-v2"):
        return handoff_for(self.by_id[decision_id])

    def assert_error(self, code, fn):
        with self.assertRaises(MemoryOperationError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def stored(self, incident_id="INC-201"):
        return self.sink.memory.list_approved_versions(incident_id)


class SinkTests(HandoffTestCase):
    def test_store_saves_the_approved_version_and_returns_none(self):
        self.assertIsNone(self.sink.store(self.handoff()))
        [item] = self.stored()
        self.assertEqual((item["approval_decision_id"], item["approved_version"]), ("DEC-201-v2", 2))
        self.assertEqual(item["report_content_ref"], self.handoff()["content_ref"])

    def test_redelivery_is_idempotent_on_decision_id(self):
        self.assertTrue(self.sink.save(self.handoff())["created"])
        self.assertFalse(self.sink.save(self.handoff())["created"])
        self.sink.store_payload(self.handoff())
        self.assertEqual(len(self.stored()), 1)

    def test_concurrent_redelivery_creates_one_record(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.sink.save(self.handoff()), range(4)))
        self.assertEqual(sum(r["created"] for r in results), 1)

    def test_a_restarted_sink_accepts_a_redelivered_handoff(self):
        self.sink.store(self.handoff())
        restarted = IncidentMemorySink.connect(self.path, self.fake)
        self.assertFalse(restarted.save(self.handoff())["created"])

    def test_change_request_never_enters_memory_even_if_handed_off(self):
        self.assert_error("NOT_APPROVED", lambda: self.sink.store(self.handoff("DEC-206-v1")))
        self.assertEqual(self.stored("INC-206"), [])

    def test_decision_unknown_to_f6_is_not_found(self):
        self.fake.decisions("RPT-201").clear()
        self.assert_error("NOT_FOUND", lambda: self.sink.store(self.handoff()))
        self.assertEqual(self.stored(), [])

    def test_version_not_shown_as_approved_by_f7_is_refused(self):
        self.fake.document("RPT-201", 2)["report_version"]["review_status"] = "pending_review"
        self.assert_error("NOT_APPROVED", lambda: self.sink.store(self.handoff()))

    def test_tampered_report_content_is_refused(self):
        self.fake.document("RPT-201", 2)["content"]["overview"]["title"] = "tampered"
        self.assert_error("VERSION_CONFLICT", lambda: self.sink.store(self.handoff()))
        self.assertEqual(self.stored(), [])

    def test_handoff_must_agree_with_f6_and_f7(self):
        changes = [
            {"content_digest": "0" * 64, "content_ref": "sha256:" + "0" * 64},
            {"reviewer_id": "SOMEONE-ELSE"},
            {"approved_at": "2026-10-05T12:31:00Z"},
            {"evidence_ids": []},
        ]
        for change in changes:
            with self.subTest(change=change):
                sink = IncidentMemorySink.connect(self.path, self.fake)
                self.assert_error("VERSION_CONFLICT", lambda: sink.store({**self.handoff(), **change}))
        self.assertEqual(self.stored(), [])

    def test_same_decision_with_different_handoff_is_a_conflict(self):
        self.sink.store(self.handoff())
        self.assert_error("VERSION_CONFLICT", lambda: self.sink.store({**self.handoff(), "evidence_ids": ["X"]}))

    def test_malformed_handoffs_are_rejected_before_any_read(self):
        good = self.handoff()
        cases = [
            {k: v for k, v in good.items() if k != "reviewer_id"},
            {**good, "extra": 1},
            {**good, "content_ref": "sha256:" + "f" * 64},
            {**good, "version": True},
            {**good, "approved_at": "2026-10-05T12:30:00"},
            {**good, "content_digest": "ABC"},
            "not an object",
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_error("VALIDATION_ERROR", lambda: self.sink.store(payload))
        self.assertEqual(self.fake.calls, 0)

    def test_review_service_outage_is_retryable_and_retry_succeeds(self):
        self.fake.fail_next = 1
        error = self.assert_error("SOURCE_UNAVAILABLE", lambda: self.sink.store(self.handoff()))
        self.assertTrue(error.retryable)
        self.assertEqual(self.stored(), [])
        self.assertTrue(self.sink.save(self.handoff())["created"])

    def test_storage_failure_is_retryable_and_retry_creates_no_duplicate(self):
        self.path.unlink()
        error = self.assert_error("STORAGE_FAILURE", lambda: self.sink.store(self.handoff()))
        self.assertTrue(error.retryable)
        sink = IncidentMemorySink.connect(self.path, self.fake)
        self.assertTrue(sink.save(self.handoff())["created"])
        self.assertFalse(sink.save(self.handoff())["created"])

    def test_late_older_approval_does_not_downgrade_current(self):
        self.sink.store(self.handoff("DEC-201-v2"))
        self.sink.store(self.handoff("DEC-201-v1"))
        current = [i for i in self.stored() if i["is_current_approved_version"]]
        self.assertEqual([i["approved_version"] for i in current], [2])

    def test_sink_requires_memory_bound_to_its_own_source(self):
        source = F6ApprovalSource(self.fake)
        self.assert_error("NOT_AUTHORIZED", lambda: IncidentMemorySink(IncidentMemory(self.path), source))

    def test_resolve_without_a_handoff_is_not_found(self):
        self.assert_error("NOT_FOUND", lambda: F6ApprovalSource(self.fake).resolve("DEC-201-v2"))

    def test_validate_handoff_accepts_a_pydantic_like_model(self):
        class Model:
            def __init__(self, data):
                self.data = data

            def model_dump(self, mode):
                assert mode == "json"
                return copy.deepcopy(self.data)

        self.assertEqual(validate_handoff(Model(self.handoff())), self.handoff())


class HttpClientTests(HandoffTestCase):
    def test_reads_approval_over_http_with_viewer_token(self):
        with ReviewServer(self.fake, token="viewer-token") as server:
            sink = IncidentMemorySink.connect(self.path, HttpReviewServiceClient(server.url, "viewer-token"))
            self.assertTrue(sink.save(self.handoff())["created"])
        self.assertEqual({r["method"] for r in server.requests}, {"GET"})
        self.assertEqual({r["path"] for r in server.requests},
                         {"/api/v1/reports/RPT-201/review", "/api/v1/reports/RPT-201/versions/2"})
        self.assertTrue(all(r["authorization"] == "Bearer viewer-token" for r in server.requests))

    def test_http_errors_map_to_typed_errors(self):
        with ReviewServer(self.fake, token="viewer-token") as server:
            client = HttpReviewServiceClient(server.url, "wrong")
            self.assert_error("NOT_AUTHORIZED", lambda: client.review("RPT-201"))
            client = HttpReviewServiceClient(server.url, "viewer-token")
            self.assert_error("NOT_FOUND", lambda: client.report_version("RPT-201", 9))
            server.force_status = 503
            self.assertTrue(self.assert_error("SOURCE_UNAVAILABLE", lambda: client.review("RPT-201")).retryable)

    def test_report_id_is_quoted_in_the_path(self):
        with ReviewServer(self.fake) as server:
            client = HttpReviewServiceClient(server.url)
            self.assert_error("NOT_FOUND", lambda: client.review("../internal/v1/x?y"))
        self.assertEqual(server.requests[0]["path"], "/api/v1/reports/..%2Finternal%2Fv1%2Fx%3Fy/review")

    def test_unreachable_service_is_retryable(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        client = HttpReviewServiceClient(f"http://127.0.0.1:{port}", timeout=2)
        self.assertTrue(self.assert_error("SOURCE_UNAVAILABLE", lambda: client.review("RPT-201")).retryable)

    def test_only_http_urls_are_accepted(self):
        for url in ("file:///etc/passwd", "ftp://host", "127.0.0.1:8095"):
            with self.subTest(url=url):
                self.assert_error("VALIDATION_ERROR", lambda: HttpReviewServiceClient(url))


if __name__ == "__main__":
    unittest.main()
