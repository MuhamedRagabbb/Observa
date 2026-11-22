"""Exercise the Docker worker with real F1 bundles and scripted provider replies."""
import json
import sqlite3
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
from correlation_agent.agent.tools import (Hypothesis, SubmitCause, _candidate, collect_supported_observations, get_structured_file, resolve, trigger_gap)
from correlation_agent.docker import run
from correlation_agent.docker.watch import discover, process_next, resume_legacy_requests


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
            self.assertEqual(report["follow_up_stop_reason"], "repeated_request")
            self.assertIn("HTTP 402", (folder / "report.md").read_text(encoding="utf-8"))
            correlation = json.loads((folder / "correlation.json").read_bytes())
            rows = [Observation.model_validate(row) for row in correlation["correlated_findings"]]
            alert = OpeningAlert(service="payments-service", family="telemetry", rule="service_signal_missing")
            self.assertIsNotNone(trigger_gap(alert, rows))

    def test_completed_legacy_request_reaches_f2_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports, output = root / "reports", root / "runs"
            config = settings()
            engine = Engine(root / "data", config)
            try:
                for t in (1000, 1010, 1020):
                    incidents = engine.tick(snapshot(config, t, "payments"))
                doc, = export(incidents, reports)
            finally:
                engine.close()
            folder = output / doc["id"]
            folder.mkdir(parents=True)
            history, _ = run.load_history(reports, doc["id"], doc["snapshot_id"])
            orch = Orchestrator({name: cls(reports, use_llm=False) for name, cls in (
                ("logs", LogsSignalAgent), ("metrics", MetricsSignalAgent), ("traces", TracesSignalAgent))})
            investigation = orch.investigate(doc)
            (folder / "investigation.initial.json").write_text(investigation.model_dump_json())
            with patch("correlation_agent.agent.graph.groq_model", return_value=AskForEvidence()):
                legacy = run.correlate(investigation.model_dump(), history=history).model_dump()
            del legacy["report"]["follow_up_answers"]
            legacy["report"]["history"].pop("opening_alert")
            for filename in ("correlation.initial.json", "correlation.json"):
                (folder / filename).write_text(json.dumps(legacy))
            with sqlite3.connect(":memory:") as db:
                discover(db, reports)
                db.execute("UPDATE report_jobs SET done=1")
                db.commit()
                resume_legacy_requests(db, reports, output)
                with patch.object(run, "make_orchestrator", return_value=orch), patch.object(
                        orch, "investigate", side_effect=AssertionError("opening analysis already exists")), patch.object(
                        orch, "follow_up", wraps=orch.follow_up) as targeted, patch(
                        "correlation_agent.agent.graph.groq_model", return_value=AskForEvidence()):
                    self.assertTrue(process_next(db, reports, output, run.process_incident))
                self.assertEqual(targeted.call_count, 1)
                follow = targeted.call_args.args[0]
                self.assertEqual(follow.request_id, legacy["follow_up_request"]["request_id"])
                self.assertEqual(follow.target_agents, ["logs"])
                published = json.loads((folder / "report.initial.json").read_bytes())
                self.assertEqual(len(published["follow_up_answers"]), 2)
                resume_legacy_requests(db, reports, output)
                self.assertFalse(process_next(db, reports, output, run.process_incident))


    def test_contradicting_evidence_is_retained_and_cannot_support_a_cause(self):
        fixture = Path(__file__).parents[2] / "investigation" / "fixtures" / "investigation_result_completed.json"
        raw = json.loads(fixture.read_bytes())
        finding = raw["logs_output"]["findings"][0]
        finding["evidence_links"].append({
            "evidence_id": finding["evidence_links"][0]["evidence_id"].replace("#P1", "#P2"),
            "relation": "contradicts", "reference_valid": True,
            "relevance_status": "relevant", "support_status": "contradicted",
        })
        report = get_structured_file(raw)
        rows = collect_supported_observations(report)
        self.assertEqual({(row.relation, row.support_status) for row in rows},
                         {("supports", "supported"), ("contradicts", "contradicted")})
        with self.assertRaisesRegex(ValueError, "Contradicting evidence"):
            _candidate(Hypothesis(service=finding["service"], explanation="test",
                                  supporting_rows=[2], related_rows=[]), rows)

    def test_source_follow_up_yields_a_citable_cause(self):
        from langchain_core.messages import HumanMessage
        from observa_investigation.agents.live_evidence import LiveEvidence

        class SelectPoolRows:
            def complete(self, messages):
                rows = json.loads(messages[-1]["content"])["rows"]
                return json.dumps([k for k in rows if k.startswith("S")])

        class ReviewPool(BaseChatModel):
            @property
            def _llm_type(self):
                return "test"

            def bind_tools(self, tools, **kwargs):
                return self

            def _generate(self, messages, **kwargs):
                context = json.loads(next(m.content for m in messages if isinstance(m, HumanMessage)))
                support = [r["row"] for r in context["observations"] if "follow-up:live-" in r["finding_id"]]
                if support:
                    name = "SubmitCause"
                    args = {"candidates": [{"service": "inventory-service", "explanation":
                            "The inventory database pool reached its capacity of 4 and acquisitions timed out.",
                            "supporting_rows": support, "related_rows": []}], "effects": []}
                else:
                    name = "RequestEvidence"
                    args = {"requests": [{"signal": s, "required": q, "reason": "Verify the inventory-service timeout"}
                                         for s, q in [("logs", "pool timeout events"), ("metrics", "pool occupancy")]],
                            "candidates": []}
                message = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "review"}])
                return ChatResult(generations=[ChatGeneration(message=message)])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reports, folder = root / "reports", root / "run"
            config = settings()
            engine = Engine(root / "data", config)
            try:
                for t in (1000, 1010, 1020):
                    sample = snapshot(config, t)
                    sample.update(origin="live")
                    sample["metrics"]["pool_timeouts"]["inventory-service"] = 3
                    incidents = engine.tick(sample)
                doc, = export(incidents, reports)
            finally:
                engine.close()

            def fetch(base, path, params):
                self.assertIn("inventory-service", params["query"])
                if base == "loki":
                    record = {"service.name": "inventory-service", "environment": "local",
                              "event": "db_pool_timeout", "error.type": "PoolAcquireTimeout"}
                    return {"status": "success", "data": {"resultType": "streams", "result": [
                        {"values": [["1000000000000", json.dumps(record)]]}]}}
                return {"status": "success", "data": {"resultType": "matrix", "result": [
                    {"values": [[1000, "4"]]}]}}

            source = LiveEvidence(folder / "follow-up-evidence", "loki", "prom", fetch)
            agents = {"logs": LogsSignalAgent(reports, use_llm=False, live=source),
                      "metrics": MetricsSignalAgent(reports, use_llm=False, live=source),
                      "traces": TracesSignalAgent(reports, use_llm=False)}
            orch = Orchestrator(agents)

            def follow(*args, **kwargs):
                for s in args[0].target_agents:
                    agents[s].llm = SelectPoolRows()
                    agents[s].use_llm = True
                return Orchestrator.follow_up(orch, *args, **kwargs)

            with patch.object(run, "make_orchestrator", return_value=orch), patch.object(
                    orch, "follow_up", side_effect=follow) as follow_call, patch(
                    "correlation_agent.agent.graph.groq_model", return_value=ReviewPool()):
                run.process_incident(reports, doc["id"], folder, doc["snapshot_id"])
            self.assertEqual(follow_call.call_count, 1)
            report = json.loads((folder / "report.json").read_bytes())
            self.assertEqual(report["status"], "probable_cause")
            self.assertEqual(report["steps"][0]["service"], "inventory-service")
            self.assertEqual({a["signal"] for a in report["follow_up_answers"]}, {"logs", "metrics"})
            for row in report["steps"][0]["evidence"]:
                key = row["evidence_ids"][0].split("#")[1]
                record = json.loads((source.directory / (key + ".json")).read_bytes())
                self.assertEqual(record["claim"], row["claim"])
                self.assertEqual(record["snapshot_id"], doc["snapshot_id"])
