"""F3 publication to F4 watcher regression checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from recommendation_agent.docker import watch


class WatchTests(TestCase):
    def test_accepts_automatic_and_manual_run_folders_and_is_idempotent(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            for folder in ("INC-a", "INC-b-20261128"):
                incident = folder.split("-2026")[0]
                f3 = runs / folder / "f3"
                f3.mkdir(parents=True)
                data = {"incident_id": incident, "run_id": f"RUN-{incident}",
                        "root_cause_candidates": [], "leading_candidate_id": None,
                        "missing_evidence": [], "status": "inconclusive", "follow_up_requests": []}
                (f3 / "output.initial.json").write_text(json.dumps(data))
                (f3 / "output.json").write_text(json.dumps(data))
            with (patch.object(watch, "RUNS", runs), patch.object(watch, "REPORTS", root / "reports"),
                  patch.object(watch, "MEMORY", root / "missing.sqlite3"),
                  patch.object(watch, "USE_LLM", False),
                  patch.object(watch, "recommend_with_state", wraps=watch.recommend_with_state) as run):
                self.assertEqual(watch.scan(), 4)
                self.assertEqual(run.call_count, 2)
                self.assertEqual(watch.scan(), 0)
            for folder in ("INC-a", "INC-b-20261128"):
                f4 = runs / folder / "f4"
                for name in ("output.initial.json", "output.json"):
                    self.assertEqual(json.loads((f4 / name).read_text())["status"], "inconclusive")
