"""Exercise the Docker worker with real F1 bundles and scripted provider replies."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr
from observa_alerts.config import settings
from observa_alerts.demo import snapshot
from observa_alerts.engine import Engine
from observa_alerts.report import export
from observa_investigation.agents.logs import LogsSignalAgent
from observa_investigation.agents.metrics import MetricsSignalAgent
from observa_investigation.agents.traces import TracesSignalAgent
from observa_investigation.orchestrator import Orchestrator

from correlation_agent.agent.state import IncidentHistory, Observation, OpeningAlert
from correlation_agent.agent.tools import Hypothesis, SubmitCause, get_structured_file, resolve, trigger_gap
from correlation_agent.docker import run


class AskForEvidence(BaseChatModel):
    _calls: int = PrivateAttr(default=0)

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, **kwargs):
        self._calls += 1
        if self._calls == 2:
            raise RuntimeError("HTTP 429")
        request = {"requests": [
            {"signal": "logs", "required": "HTTP 402 in payments-service", "reason": "Confirm a decline."},
            {"signal": "logs", "required": "HTTP 599 in payments-service", "reason": "Check the alleged status."}],
            "candidates": []}
        message = AIMessage(content="", tool_calls=[{"name": "RequestEvidence", "args": request, "id": "ask"}])
        return ChatResult(generations=[ChatGeneration(message=message)])


class SelectEvidence:
    def complete(self, messages):
        query = json.loads(messages[-1]["content"])
        status = "HTTP 402" if "402" in query["question"] else "HTTP 599"
        return json.dumps([k for k, claim in query["rows"].items() if status in claim and "payments-service" in claim])


class InvestigationFlowTests(unittest.TestCase):
    def test_opening_alert_changes_which_decision_is_accepted(self):
        service = "orders-service"
        history = IncidentHistory(first_snapshot="r1", snapshot_id="r1", phase="initial",
            first_observed_at="2026-01-01T00:00:00Z", resolved_at=None, peaks=[], dependencies=[],
            release_findings=[], affected_services=[service])
        report = get_structured_file(dict(incident_id="INC-demo", run_id="RUN-demo",
            investigation_plan_id="PLAN", status="completed",
            **{s + "_output": {"status": "completed"} for s in ("logs", "traces", "metrics")}))
        rows = [Observation(service=service, signal=s, snapshot_id="r1", finding_id=s, claim=indicator,
                indicator=indicator, evidence_ids=[f"INC-demo/r1/{s}#1"])
                for s, indicator in [("metrics", "pool_timeouts"), ("logs", "db_pool_timeout")]]
        decision = SubmitCause(candidates=[Hypothesis(service=service, explanation="Database pool timeout",
                                supporting_rows=[1, 2], related_rows=[])], effects=[])
        history.opening_alert = OpeningAlert(service=service, family="database", rule="pool_timeouts")
        self.assertEqual(resolve(decision, report, rows, [], history).report.status, "probable_cause")
        history.opening_alert = OpeningAlert(service=service, family="telemetry", rule="service_signal_missing")
        result = resolve(decision, report, rows, [], history)
        self.assertIsNone(result.final_root_cause)
        self.assertEqual(result.follow_up_request.target_agents, ["logs"])

    def test_provider_retry_resumes_after_targeted_follow_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = settings()
            engine = Engine(root / "data", config)
            try:
                for t in (1000, 1010, 1020):
                    sample = snapshot(config, t, "payments")
                    sample["logs"].append({"service.name": "payments-service", "event": "request_completed",
                        "environment": "local", "level": "ERROR", "http.status_code": 402, "observed_at": t})
                    incidents = engine.tick(sample)
                doc, = export(incidents, root / "reports")
            finally:
                engine.close()
            folder = root / "runs"
            model = AskForEvidence()
            agents = {
                "logs": LogsSignalAgent(root / "reports", use_llm=False),
                "metrics": MetricsSignalAgent(root / "reports", use_llm=False),
                "traces": TracesSignalAgent(root / "reports", use_llm=False),
            }
            orch = Orchestrator(agents)
            def follow(*args, **kwargs):
                agents["logs"].use_llm = True
                agents["logs"].llm = SelectEvidence()
                return Orchestrator.follow_up(orch, *args, **kwargs)
            args = (root / "reports", doc["id"], folder, doc["snapshot_id"])
            with patch.object(run, "make_orchestrator", return_value=orch), patch(
                    "correlation_agent.agent.graph.groq_model", return_value=model), patch.object(
                    orch, "investigate", wraps=orch.investigate) as initial, patch.object(
                    orch, "follow_up", side_effect=follow) as targeted:
                with self.assertRaisesRegex(RuntimeError, "429"):
                    run.process_incident(*args)
                run.process_incident(*args)
                self.assertEqual(initial.call_count, 1)
                self.assertEqual(targeted.call_count, 1)
            investigation = json.loads((folder / "investigation.json").read_bytes())
            found, missing = investigation["logs_output"]["answers"]
            self.assertIn("HTTP 402", found["answer"])
            self.assertTrue(found["evidence_ids"])
            self.assertEqual(missing["evidence_ids"], [])
            self.assertIn("retained sample", missing["answer"])
            self.assertNotIn("answers", investigation["metrics_output"])
            report = json.loads((folder / "report.json").read_bytes())
            self.assertEqual(report["follow_up_answers"], [found, missing])
            self.assertEqual(report["status"], "needs_more_analysis")
            self.assertIn("HTTP 402", (folder / "report.md").read_text(encoding="utf-8"))
            correlation = json.loads((folder / "correlation.json").read_bytes())
            rows = [Observation.model_validate(row) for row in correlation["correlated_findings"]]
            alert = OpeningAlert(service="payments-service", family="telemetry", rule="service_signal_missing")
            self.assertIsNotNone(trigger_gap(alert, rows))
