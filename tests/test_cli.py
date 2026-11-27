import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from observa.f5_memory.f6_handoff import FakeReviewService, handoff_for
from tests.review_server import ReviewServer

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class CliTests(unittest.TestCase):
    def test_demo_across_processes_and_repeat_seed(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = [sys.executable, "-m", "observa.f5_memory", "--db", str(Path(directory) / "demo.sqlite3")]

            def run(*args):
                result = subprocess.run([*prefix, *args], capture_output=True, text=True, check=True)
                return json.loads(result.stdout)

            seed = run("seed-demo")
            self.assertEqual(sum(r["status"] == "excluded" for r in seed["results"]), 2)
            self.assertEqual(sum(r.get("created", False) for r in seed["results"]), 8)
            items = run("search", "connection pool timeout", "--service", "orders-service")["items"]
            self.assertEqual(items[0]["source_incident_id"], "INC-201")
            self.assertEqual(items[0]["approved_version"], 2)
            self.assertEqual(run("get", items[0]["knowledge_id"])["approval_decision_id"], "DEC-201-v2")
            self.assertEqual(len(run("history", "INC-201")["items"]), 2)
            self.assertFalse(any(r.get("created", False) for r in run("seed-demo")["results"]))
            self.assertEqual(run("search", "zebras-with-no-matches")["items"], [])

    def test_cli_missing_record_has_nonzero_exit_and_typed_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-m", "observa.f5_memory", "--db",
                str(Path(directory) / "demo.sqlite3"), "get", "unknown"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["error"]["code"], "NOT_FOUND")

    def test_import_handoffs_reads_f6_files_and_checks_them_against_the_service(self):
        snapshots = json.loads((FIXTURES / "approval_snapshots.json").read_text(encoding="utf-8"))
        by_id = {s["decision"]["decision_id"]: s for s in snapshots}
        with tempfile.TemporaryDirectory() as directory, ReviewServer(FakeReviewService(snapshots), "viewer-token") as server:
            handoffs = Path(directory) / "memory"
            handoffs.mkdir()
            for decision_id in ("DEC-201-v1", "DEC-201-v2", "DEC-206-v1"):
                (handoffs / f"{decision_id}.json").write_text(json.dumps(handoff_for(by_id[decision_id])))
            (handoffs / "broken.json").write_text("{not json")
            command = [sys.executable, "-m", "observa.f5_memory", "--db", str(Path(directory) / "m.sqlite3"),
                       "import-handoffs", str(handoffs), "--review-url", server.url]
            env = {**os.environ, "OBSERVA_REVIEW_TOKEN": "viewer-token"}
            result = subprocess.run(command, capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("viewer-token", result.stdout + result.stderr)
            outcomes = {r["file"]: r for r in json.loads(result.stdout)["results"]}
            self.assertTrue(outcomes["DEC-201-v1.json"]["created"])
            self.assertTrue(outcomes["DEC-201-v2.json"]["created"])
            self.assertEqual(outcomes["DEC-206-v1.json"]["error"]["code"], "NOT_APPROVED")
            self.assertEqual(outcomes["broken.json"]["error"]["code"], "VALIDATION_ERROR")
            again = json.loads(subprocess.run(command, capture_output=True, text=True, env=env).stdout)["results"]
            self.assertFalse(any(r.get("created") for r in again))


if __name__ == "__main__":
    unittest.main()
