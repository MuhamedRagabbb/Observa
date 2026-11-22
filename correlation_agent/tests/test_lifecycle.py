import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from correlation_agent.agent.history import failed_trace_edges, release_findings
from correlation_agent.docker import watch
from correlation_agent.docker.watch import discover, process_next


class LifecycleTests(unittest.TestCase):
    def test_open_close_retry_restart_and_closed_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.sqlite3"
            path = root / "INC-a" / "incident.json"
            path.parent.mkdir()
            def update(status, sid):
                path.write_text(json.dumps(dict(id="INC-a", status=status, snapshot_id=sid)))
            seen = []
            def process(reports, iid, folder, sid):
                seen.append((iid, sid))
            with sqlite3.connect(queue) as db:
                update("open", "r1")
                discover(db, root)
                update("open", "r2")
                discover(db, root)
                self.assertTrue(process_next(db, root, root, process))
            db.close()
            with sqlite3.connect(queue) as db:
                update("resolved", "r3")
                other = root / "INC-b"
                other.mkdir()
                (other / "incident.json").write_text(json.dumps(dict(id="INC-b", status="resolved", snapshot_id="r8")))
                discover(db, root)
                def fail(*args):
                    raise RuntimeError("offline")
                process_next(db, root, root, fail)
                process_next(db, root, root, process)
                db.execute("UPDATE report_jobs SET retry_at=0")
                db.commit()
                process_next(db, root, root, process)
                discover(db, root)
                self.assertFalse(process_next(db, root, root, process))
            db.close()
            self.assertEqual(seen, [("INC-a", "r1"), ("INC-b", "r8"), ("INC-a", "r3")])


    def test_retries_are_bounded_and_processing_does_not_hold_sqlite_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue = root / "queue.sqlite3"
            incident = root / "INC-a"
            incident.mkdir()
            (incident / "incident.json").write_text(json.dumps(
                dict(id="INC-a", status="resolved", snapshot_id="r1")))
            with closing(sqlite3.connect(queue)) as db:
                discover(db, root)
                original_limit = watch.MAX_ATTEMPTS
                watch.MAX_ATTEMPTS = 2
                try:
                    def process(*_):
                        with closing(sqlite3.connect(queue, timeout=0.1)) as other:
                            other.execute("CREATE TABLE IF NOT EXISTS concurrent_write (value INTEGER)")
                            other.execute("INSERT INTO concurrent_write VALUES (1)")
                        raise RuntimeError("offline")

                    self.assertTrue(process_next(db, root, root, process))
                    self.assertEqual(db.execute("SELECT attempts, done FROM report_jobs").fetchone(), (1, 0))
                    db.execute("UPDATE report_jobs SET retry_at=0")
                    db.commit()
                    self.assertTrue(process_next(db, root, root, process))
                    attempts, done, error = db.execute(
                        "SELECT attempts, done, last_error FROM report_jobs").fetchone()
                    self.assertEqual((attempts, done), (2, 1))
                    self.assertIn("offline", error)
                    self.assertFalse(process_next(db, root, root, process))
                finally:
                    watch.MAX_ATTEMPTS = original_limit

    def test_release_requires_service_version_and_time(self):
        base = {"service.name": "orders", "service.version": "v2", "deployment.id": "d"}
        change = dict(base, event="release_changed", timestamp="2026-01-01T00:00:02Z")
        error = dict(base, level="ERROR", timestamp="2026-01-01T00:00:03Z", **{"error.type": "WriteError"})
        rows = [("logs#0", change), ("logs#1", error)]
        match = list(release_findings(rows, "INC-a", "r1", ["orders"]))
        self.assertEqual(match[0][1].evidence_ids, ["logs#0", "logs#1"])
        for overrides in [{"service.name": "other"}, {"service.version": "v1"}, {"deployment.id": "z"},
                          {"timestamp": "2026-01-01T00:00:01Z"}]:
            self.assertFalse(list(release_findings([rows[0], ("x", {**error, **overrides})], "INC-a", "r1", ["orders"])))

    def test_trace_requires_failed_direct_parent_in_same_trace(self):
        caller = {"span_id": "a", "service": "orders", "error": True}
        parent = {"refType": "CHILD_OF", "spanID": "a", "traceID": "T"}
        child = {"span_id": "b", "service": "payments", "error": True, "references": [parent]}
        def edges(span):
            return list(failed_trace_edges([("parent", caller), ("child", span)], "T", ["orders", "payments"]))
        self.assertEqual([(e.caller, e.callee) for e in edges(child)], [("orders", "payments")])
        for update in [{"error": False}, {"references": []}, {"references": [{**parent, "traceID": "U"}]}]:
            self.assertFalse(edges({**child, **update}))
