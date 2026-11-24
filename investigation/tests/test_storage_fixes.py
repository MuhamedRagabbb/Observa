"""Regression tests for the ledger and checkpointer storage fixes."""
import os
import pathlib
import sqlite3
import tempfile
import unittest

from observa_investigation.checkpointer import SqliteSaver
from observa_investigation.contracts import InvestigationResult
from observa_investigation.service import InvestigationLedger


FIXTURE = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "investigation_result_completed.json"


def _result(run_id="RUN-1", incident_id="INC-1"):
    base = InvestigationResult.model_validate_json(FIXTURE.read_text(encoding="utf-8"))
    return base.model_copy(update={"run_id": run_id, "incident_id": incident_id})


class LedgerStorageTests(unittest.TestCase):
    def test_in_memory_ledger_keeps_its_tables_between_calls(self):
        ledger = InvestigationLedger(":memory:")
        ledger.record_run(_result("RUN-1"), origin="initial")
        ledger.record_run(_result("RUN-2"), origin="correlation", request_id="REQ-1")
        self.assertEqual(ledger.get_run("RUN-1").run_id, "RUN-1")
        self.assertEqual(ledger.get_by_request_id("REQ-1").run_id, "RUN-2")
        self.assertEqual(ledger.count_reinvestigations("INC-1"), 1)
        self.assertEqual(ledger.count_review_cycles("INC-1"), 0)

    def test_file_ledger_survives_reopening(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ledger.db")
            InvestigationLedger(path).record_run(_result("RUN-9"), origin="human_feedback")
            again = InvestigationLedger(path)
            self.assertEqual(again.get_run("RUN-9").run_id, "RUN-9")
            self.assertEqual(again.count_review_cycles("INC-1"), 1)


class CheckpointerStorageTests(unittest.TestCase):
    def _cfg(self):
        return {"configurable": {"thread_id": "T1", "checkpoint_ns": ""}}

    def test_metadata_round_trips_with_the_installed_serializer(self):
        saver = SqliteSaver(":memory:")
        cp = {"id": "0001", "v": 1, "ts": "2026-11-25T00:00:00+00:00",
              "channel_values": {}, "channel_versions": {}, "versions_seen": {}}
        meta = {"source": "loop", "step": 3, "parents": {}}
        saver.put(self._cfg(), cp, meta, {})
        got = saver.get_tuple(self._cfg())
        self.assertEqual(got.metadata["step"], 3)
        self.assertEqual(got.metadata["source"], "loop")

    def test_old_database_without_metadata_type_is_migrated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.db")
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE checkpoints (thread_id TEXT, checkpoint_ns TEXT, checkpoint_id TEXT, "
                "parent_id TEXT, type TEXT, checkpoint BLOB, metadata BLOB, "
                "PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id))"
            )
            conn.commit()
            conn.close()
            saver = SqliteSaver(path)
            try:
                cols = {r[1] for r in saver.conn.execute("PRAGMA table_info(checkpoints)")}
                self.assertIn("metadata_type", cols)
            finally:
                saver.close()


if __name__ == "__main__":
    unittest.main()
