"""Exercise the Docker worker with real F1 bundles and scripted provider replies."""
import json
import os
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

from correlation_agent.agent.contract import failed_contract_result, to_contract_result
from correlation_agent.agent.state import IncidentHistory, Observation, OpeningAlert, InvestigationResult as F3InvestigationResult
from correlation_agent.agent.tools import (EvidenceRequest, Hypothesis, RequestEvidence, SubmitCause, _candidate,
    collect_supported_observations, collect_unresolved_evidence, get_structured_file, resolve, trigger_gap)
from observa.f5_memory.service import IncidentMemory
from observa.f5_memory.sources import FakeApprovalSource
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
    def test_f2_contract_type_is_loaded_from_one_canonical_module(self):
        from observa_investigation.contracts import InvestigationResult

        self.assertIs(F3InvestigationResult, InvestigationResult)

    def test_failed_f3_contract_includes_error_envelope(self):
        result = failed_contract_result("INC-a", "final", "r8", "provider rejected request",
            retryable=False, error_type="ProviderRequestError", status_code=400)
        public = result.model_dump()
        self.assertEqual(public["status"], "failed")
        self.assertIsNone(public["leading_candidate_id"])
        self.assertEqual(public["error"]["code"], "INVESTIGATION_FAILED")
        self.assertFalse(public["error"]["retryable"])

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

    def test_empty_and_unavailable_memory_are_distinct(self):
        fixture = Path(__file__).resolve().parents[2] / "fixtures" / "f1_incident_sample.json"
        incident = json.loads(fixture.read_bytes())["incidents"]["open"]
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "memory.sqlite3"
            lookup = run.WorkerMemoryLookup(incident, [])
            with patch.dict(os.environ, OBSERVA_MEMORY_DB=str(database)):
                self.assertEqual(lookup(incident["id"], []), [])
                self.assertEqual(lookup.summary.status, "unavailable")
                self.assertFalse(database.exists())
                IncidentMemory(database)
                self.assertEqual(lookup(incident["id"], []), [])
                self.assertEqual(lookup.summary.status, "no_match")

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
            database = root / "memory.sqlite3"
            fixture = Path(__file__).resolve().parents[2] / "fixtures" / "approval_snapshots.json"
            memory = IncidentMemory(database, FakeApprovalSource.from_file(fixture))
            memory.save_approved_incident("DEC-202-v1")
            args = (root / "reports", doc["id"], folder, doc["snapshot_id"])
            with patch.dict(os.environ, OBSERVA_MEMORY_DB=str(database)), patch.object(run, "make_orchestrator", return_value=orch), patch(
                    "correlation_agent.agent.graph.groq_model", return_value=model), patch.object(
                    orch, "investigate", wraps=orch.investigate) as initial, patch.object(
                    orch, "follow_up", side_effect=follow) as targeted:
                with self.assertRaisesRegex(RuntimeError, "429"):
                    run.process_incident(*args)
                run.process_incident(*args)
                self.assertEqual(initial.call_count, 1)
                self.assertEqual(targeted.call_count, 1)
            investigation = json.loads((folder / "f2" / "investigation.initial.json").read_bytes())
            found, missing = investigation["logs_output"]["answers"]
            self.assertIn("HTTP 402", found["answer"])
            self.assertTrue(found["evidence_ids"])
            self.assertEqual(missing["evidence_ids"], [])
            self.assertIn("retained sample", missing["answer"])
            self.assertEqual(investigation["metrics_output"]["answers"], [])
            report = json.loads((folder / "f3" / "output.initial.json").read_bytes())
            self.assertIn("Follow-up evidence", (folder / "f3" / "output.initial.md").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "inconclusive")
            self.assertEqual(report["follow_up_requests"][0]["status"], "rejected")
            self.assertIn("HTTP 402", (folder / "f3" / "output.initial.md").read_text(encoding="utf-8"))
            correlation = json.loads((folder / "f3" / ".state" / "correlation.initial.json").read_bytes())
            rows = [Observation.model_validate(row) for row in correlation["correlated_findings"]]
            audit = correlation["report"]["memory_search"]
            self.assertEqual(audit["status"], "matched")
            self.assertEqual(audit["matches"][0]["source_incident_id"], "INC-202")
            self.assertTrue(audit["matches"][0]["evidence_ids"])
            self.assertIn("INC-202", correlation["relevant_data_from_memory"][0])
            self.assertIn("INC-202", (folder / "f3" / "output.md").read_text(encoding="utf-8"))
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
            (folder / "f2").mkdir(parents=True, exist_ok=True)
            (folder / "f2" / "investigation.initial.json").write_text(investigation.model_dump_json())
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
                published = json.loads((folder / "f3" / "output.initial.json").read_bytes())
                self.assertEqual(published["status"], "inconclusive")
                self.assertEqual(published["follow_up_requests"][0]["status"], "rejected")
                self.assertEqual(len(published["follow_up_requests"]), 1)
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

    def test_invalid_evidence_reference_is_reported_as_unverified(self):
        fixture = Path(__file__).parents[2] / "investigation" / "fixtures" / "investigation_result_completed.json"
        raw = json.loads(fixture.read_bytes())
        finding = raw["logs_output"]["findings"][1]
        finding["claim_kind"] = "observation"
        finding["evidence_links"][0]["support_status"] = "supported"
        finding["evidence_links"].append({
            "evidence_id": "INC-wrong/r1/metrics#bad-ref", "relation": "supports",
            "reference_valid": False, "relevance_status": "relevant", "support_status": "supported",
        })
        report = get_structured_file(raw)
        unresolved = collect_unresolved_evidence(report)
        self.assertEqual(len(unresolved), 1)
        rows = collect_supported_observations(report)
        log_row = next(i for i, row in enumerate(rows, 1)
                       if row.service == finding["service"] and row.signal == "logs")
        metric_row = next(i for i, row in enumerate(rows, 1)
                          if row.service == finding["service"] and row.signal == "metrics")
        result = resolve(SubmitCause(candidates=[Hypothesis(service=finding["service"],
            explanation="Supported by logs and metrics", supporting_rows=[log_row, metric_row],
            related_rows=[])], effects=[]), report, rows, [], unresolved_evidence=unresolved)
        public = to_contract_result(result).model_dump()
        reported = [gap for gap in public["missing_evidence"]
                    if gap["required"].endswith("INC-wrong/r1/metrics#bad-ref")]
        self.assertEqual(len(reported), 1)
        self.assertEqual(reported[0]["status"], "unverified")
        candidate = next(item for item in public["root_cause_candidates"]
                         if item["candidate_id"] == public["leading_candidate_id"])
        invalid_link = next(link for link in candidate["evidence_links"]
                            if link["evidence_id"] == "INC-wrong/r1/metrics#bad-ref")
        self.assertEqual(invalid_link["reference_valid"], "unknown")
        self.assertEqual(invalid_link["support_status"], "unverified")

    def test_exhausted_follow_up_maps_to_inconclusive_contract_status(self):
        fixture = Path(__file__).parents[2] / "investigation" / "fixtures" / "investigation_result_completed.json"
        report = get_structured_file(json.loads(fixture.read_bytes()))
        result = resolve(RequestEvidence(requests=[EvidenceRequest(signal="logs", required="more logs",
            reason="No origin evidence yet")], candidates=[]), report, [], [])
        result.report.follow_up_stop_reason = "round_limit"
        self.assertEqual(to_contract_result(result).status, "inconclusive")

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

            source = LiveEvidence(folder / "f2" / "follow-up-evidence", "loki", "prom", fetch)
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
            report = json.loads((folder / "f3" / "output.initial.json").read_bytes())
            self.assertEqual(set(report), {"incident_id", "run_id", "root_cause_candidates",
                                           "leading_candidate_id", "missing_evidence", "status",
                                           "follow_up_requests"})
            self.assertEqual(report["status"], "completed")
            self.assertIsNotNone(report["leading_candidate_id"])
            self.assertEqual(set(report["root_cause_candidates"][0]),
                             {"candidate_id", "description", "evidence_links", "uncertainty"})
            self.assertEqual(len(report["root_cause_candidates"]), 1)
            self.assertEqual({link["evidence_id"].split("/")[2].split("#")[0] for link in report["root_cause_candidates"][0]["evidence_links"]}, {"logs", "metrics"})
            markdown = (folder / "f3" / "output.initial.md").read_text(encoding="utf-8")
            self.assertIn("inventory-service", markdown)
            self.assertTrue((folder / "f2" / "investigation.initial.json").is_file())
            self.assertTrue((folder / "f3" / ".state" / "correlation.initial.json").is_file())
            for path in source.directory.glob("live-*.json"):
                record = json.loads(path.read_bytes())
                self.assertEqual(record["snapshot_id"], doc["snapshot_id"])
