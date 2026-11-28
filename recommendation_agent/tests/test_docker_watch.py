"""F3 publication to F4 watcher regression checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from recommendation_agent.docker import watch


class WatchTests(TestCase):
    def test_old_validation_is_refreshed_from_saved_options_without_llm(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            f3 = root / "runs" / "INC-a" / "f3"
            f3.mkdir(parents=True)
            (f3 / "output.initial.json").write_text(json.dumps({
                "incident_id": "INC-a", "run_id": "RUN-a", "root_cause_candidates": [],
                "leading_candidate_id": None, "missing_evidence": [],
                "status": "inconclusive", "follow_up_requests": [],
            }))
            with (patch.object(watch, "REPORTS", root / "reports"),
                  patch.object(watch, "MEMORY", root / "missing.sqlite3"),
                  patch.object(watch, "USE_LLM", False)):
                self.assertTrue(watch.process(f3 / "output.initial.json"))
                stamp = root / "runs" / "INC-a" / "f4" / "input.initial.json"
                prior = json.loads(stamp.read_text())
                prior.pop("validation_revision")
                stamp.write_text(json.dumps(prior))
                with (patch.object(watch, "recommend_with_state", side_effect=AssertionError("LLM rerun")),
                      patch.object(watch, "validate_recommendation_evidence",
                                   wraps=watch.validate_recommendation_evidence) as validate):
                    self.assertTrue(watch.process(f3 / "output.initial.json"))
                    self.assertEqual(validate.call_count, 1)
                self.assertEqual(json.loads(stamp.read_text())["validation_revision"], watch.VALIDATION_REVISION)

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

    def test_provider_failure_waits_before_retry_and_does_not_run_latest_first(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            f3 = root / "runs" / "INC-a" / "f3"
            f3.mkdir(parents=True)
            data = {"incident_id": "INC-a", "run_id": "RUN-a", "root_cause_candidates": [],
                    "leading_candidate_id": None, "missing_evidence": [],
                    "status": "inconclusive", "follow_up_requests": []}
            for name in ("output.initial.json", "output.json"):
                (f3 / name).write_text(json.dumps(data))
            with (patch.object(watch, "RUNS", root / "runs"),
                  patch.object(watch, "REPORTS", root / "reports"),
                  patch.object(watch, "MEMORY", root / "missing.sqlite3"),
                  patch.object(watch, "USE_LLM", True),
                  patch.object(watch, "recommend_with_state", side_effect=RuntimeError("provider unavailable")) as run):
                self.assertEqual(watch.scan(), 0)
                self.assertEqual(watch.scan(), 0)
                self.assertEqual(run.call_count, 1)
