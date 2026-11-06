import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from observa_alerts.config import settings
from observa_alerts.demo import snapshot
from observa_alerts.engine import Engine
from observa_alerts.report import export
from observa_alerts.collect import collect


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = settings()
        self.engine = Engine(self.tmp.name, self.config)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def run_case(self, case, start=1000):
        for t in range(start, start+31, 10):
            incidents = self.engine.tick(snapshot(self.config, t, case))
        return incidents

    def test_normal_noise_has_no_incidents(self):
        self.assertEqual(self.run_case(None), [])

    def test_payments_dependency_cascade_groups(self):
        incidents = self.run_case("payments")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(set(incidents[0]["affected_services"]), {"payments-service", "orders-service", "api-gateway"})
        self.assertTrue(any(r["kind"] == "failed_dependency" for r in incidents[0]["relationships"]))
        self.assertEqual(incidents[0]["root_cause"]["status"], "not_determined")

    def test_pool_and_deployment_detect(self):
        for scenario in ("pool", "deployment"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as d:
                engine = Engine(d, self.config)
                try:
                    for t in (1000,1010,1020):
                        incidents = engine.tick(snapshot(self.config, t, scenario))
                    self.assertEqual(len(incidents), 1)
                    self.assertIn("orders-service", incidents[0]["affected_services"])
                finally:
                    engine.close()

    def test_dependency_relationship_names_actual_downstream_alert(self):
        incident = self.run_case("payments")[0]
        for alert in incident["alerts"].values():
            self.assertEqual(alert["incident_id"], incident["id"])
        dependencies = [r for r in incident["relationships"] if r["kind"] == "failed_dependency"]
        self.assertTrue(dependencies)
        for r in dependencies:
            self.assertEqual(incident["alerts"][r["alert_id"]]["service"], r["direction"]["upstream"])
            self.assertEqual(incident["alerts"][r["related_alert_id"]]["service"], r["direction"]["downstream"])
        self.assertTrue(any(r["direction"] == {"upstream": "api-gateway", "downstream": "orders-service"}
                            for r in dependencies))

    def test_trace_grouping_names_an_evidenced_member(self):
        for t in (1000, 1010, 1020):
            s = snapshot(self.config, t, "payments")
            s["logs"] = []
            incidents = self.engine.tick(s)
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        traces = [r for r in incident["relationships"] if r["kind"] == "shared_failed_trace"]
        self.assertTrue(traces)
        for r in traces:
            self.assertIn(r["alert_id"], incident["alerts"])
            self.assertEqual(incident["alerts"][r["related_alert_id"]]["service"], r["related_service"])
            self.assertEqual(r["trace_ids"], ["a" * 32])

    def test_export_updates_same_files_and_keeps_resolved_incidents_after_restart(self):
        reports = Path(self.tmp.name) / "reports"
        incident = self.run_case("payments")[0]
        ident, revision = incident["id"], incident["revision"]
        export([incident], reports)
        for t in range(1040, 1120, 10):
            incidents = self.engine.tick(snapshot(self.config, t))
        self.engine.close()
        self.engine = Engine(self.tmp.name, self.config)
        incidents = self.engine.tick(snapshot(self.config, 1120))
        export(incidents, reports)
        saved = json.loads((reports / (ident + ".json")).read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "resolved")
        self.assertGreater(saved["revision"], revision)
        self.assertEqual(len(list(reports.glob("INC-*.json"))), 1)
        index = json.loads((reports / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index[0]["alert_ids"], sorted(saved["alerts"]))
        self.assertEqual(index[0]["alert_count"], len(saved["alerts"]))
        self.assertIn(ident + ".md", (reports / "index.md").read_text(encoding="utf-8"))
        readable = (reports / (ident + ".md")).read_text(encoding="utf-8")
        self.assertIn("alerts below belong to incident **" + ident, readable)
        self.assertIn("confidence: **high**", readable)
        self.assertEqual(list(reports.glob("*.tmp")), [])

    def test_empty_report_index_is_explicit(self):
        reports = Path(self.tmp.name) / "reports"
        export([], reports)
        self.assertEqual(json.loads((reports / "index.json").read_text()), [])
        self.assertIn("No incidents have been created yet", (reports / "index.md").read_text())

    def test_402_declines_detect_without_5xx(self):
        incidents = self.run_case("declines")
        self.assertEqual(len(incidents), 1)
        self.assertEqual({a["rule"] for a in incidents[0]["alerts"].values()}, {"payment_failures"})

    def test_low_volume_does_not_fire_ratio(self):
        for t in (1000,1010,1020,1030):
            s = snapshot(self.config,t,"declines")
            s["metrics"]["payment_requests"]["payments-service"] = 2
            incidents = self.engine.tick(s)
        self.assertEqual(incidents, [])

    def test_time_only_does_not_group_services(self):
        for t in (1000,1010,1020):
            s=snapshot(self.config,t)
            s["metrics"]["http_errors"]["inventory-service"]=.8
            s["metrics"]["http_errors"]["payments-service"]=.8
            incidents=self.engine.tick(s)
        self.assertEqual(len(incidents),2)

    def test_same_service_independent_specific_families_stay_separate(self):
        for t in (1000,1010,1020):
            s=snapshot(self.config,t,"deployment")
            s["metrics"]["pool_timeouts"]["orders-service"]=15
            incidents=self.engine.tick(s)
        self.assertEqual(len(incidents),2)

    def test_transient_does_not_fire(self):
        self.engine.tick(snapshot(self.config,1000,"payments"))
        self.assertEqual(self.engine.tick(snapshot(self.config,1010)),[])

    def test_outage_does_not_close_business_incident(self):
        incident=self.run_case("payments")[0]
        for t in range(1040,1140,10):
            s=snapshot(self.config,t)
            s["metrics"]={}
            s["source_health"]={"prometheus":"TimeoutError","loki":"TimeoutError","jaeger":"not_queried"}
            incidents=self.engine.tick(s)
        old=next(i for i in incidents if i["id"]==incident["id"])
        self.assertEqual(old["status"],"open")
        self.assertTrue(old["data_gaps"])
        self.assertTrue(any(i["anchor"]["family"]=="telemetry" for i in incidents))

    def test_recovery_and_recurrence_create_new_incident(self):
        original=self.run_case("payments")[0]["id"]
        for t in range(1040,1120,10):
            incidents=self.engine.tick(snapshot(self.config,t))
        self.assertEqual(incidents[0]["status"],"resolved")
        incidents=self.run_case("payments",1120)
        self.assertEqual(len(incidents),2)
        self.assertNotEqual(next(i for i in incidents if i["status"]=="open")["id"],original)

    def test_restart_preserves_identity(self):
        ident=self.run_case("payments")[0]["id"]
        self.engine.close()
        self.engine=Engine(self.tmp.name,self.config)
        incidents=self.engine.tick(snapshot(self.config,1040,"payments"))
        self.assertEqual(len(incidents),1)
        self.assertEqual(incidents[0]["id"],ident)

    def test_duplicate_and_out_of_order_rejected(self):
        self.engine.tick(snapshot(self.config,1000))
        for t in (1000,990):
            with self.assertRaises(ValueError):
                self.engine.tick(snapshot(self.config,t))

    def test_gap_resets_pending_timer(self):
        self.engine.tick(snapshot(self.config,1000,"payments"))
        self.assertEqual(self.engine.tick(snapshot(self.config,1100,"payments")),[])

    def test_collector_cached_uptime_becomes_unknown(self):
        for t in range(1000,1090,10):
            s=snapshot(self.config,t)
            s["metrics"]["uptime"]["orders-service"]=100
            incidents=self.engine.tick(s)
        self.assertTrue(any(i["anchor"]=={"service":"orders-service","family":"telemetry"} for i in incidents))

    def test_export_contains_handoff_contract(self):
        incidents=self.run_case("payments")
        export(incidents,Path(self.tmp.name)/"reports")
        report=json.loads((Path(self.tmp.name)/"reports"/(incidents[0]["id"]+".json")).read_text())
        for key in ("schema_version","alerts","relationships","evidence","timeline","source_health","data_gaps","handoff","origin"):
            self.assertIn(key,report)
        self.assertTrue(report["evidence"]["logs"])
        self.assertTrue(report["evidence"]["traces"])
        self.assertTrue(report["handoff"]["ready_for_investigation"])

    def test_source_failure_is_explicit(self):
        with patch("observa_alerts.collect.get_json", side_effect=TimeoutError()):
            s=collect(self.config,1000)
        self.assertEqual(s["metrics"],{})
        self.assertNotEqual(s["source_health"]["prometheus"],"ok")

    def test_log_truncation_is_not_healthy(self):
        def fake(base,path,params):
            if "prometheus" in base or path=="/api/v1/query":
                return {"status":"success","data":{"resultType":"vector","result":[]}}
            return {"status":"success","data":{"resultType":"streams","result":[{"values":[["1000000000000",json.dumps({"service.name":"orders-service","environment":"local","level":"WARN"})]]}]}}
        self.config["log_limit"]=1
        with patch("observa_alerts.collect.get_json",side_effect=fake):
            s=collect(self.config,1000)
        self.assertEqual(s["source_health"]["loki"],"truncated")

if __name__=="__main__":
    unittest.main()
