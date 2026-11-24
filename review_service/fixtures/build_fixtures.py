"""Build the F1-F4 mock fixtures used to develop and test F6/F7 without the upstream features.

Payments fixtures are derived from a real local run (F1 incident bundle, F2 result and
the F3 public contract output); the DB-exhaustion and bad-deployment scenarios are
synthetic but follow the producers' formats. Every object is validated with the
F7 inbound models before it is written, and the output is deterministic.

    python review_service/fixtures/build_fixtures.py \
        --f1-incident <alert-detection-grouping/reports/INC-.../incident.json> \
        --f2-investigation <correlation_agent/runs/INC-.../f2/investigation.initial.json> \
        --f3-correlation <F3 public output.json of that run> \
        --f3-failed <F3 failed_contract_result JSON for that incident>
"""
import argparse
import hashlib
import json
from pathlib import Path

from observa_investigation.contracts import InvestigationResult
from observa_reports.inputs import CorrelationResultIn, F1Output, RecommendationResultIn, ReportInputs

OUT = Path(__file__).resolve().parent
REPO = OUT.parents[1]
SEVERITY = {"warning": "medium", "critical": "critical"}


def sha(text: str, n: int) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


def write(group: str, name: str, value) -> None:
    path = OUT / group / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def link(evidence_id, relation="supports", support="supported", valid=True, relevance="relevant"):
    return {"evidence_id": evidence_id, "relation": relation, "reference_valid": valid,
            "relevance_status": relevance, "support_status": support}


def finding(iid, sid, signal, n, service, claim, row, kind="observation", indicator=None, uncertainty=None):
    support = "supported" if kind == "observation" else "unverified"
    return {"indicator": indicator, "finding_id": f"{iid}:{signal}:F{n}", "signal_type": signal,
            "service": service, "claim": claim, "claim_kind": kind,
            "evidence_links": [link(f"{iid}/{sid}/{signal}#{row}", support=support)],
            "execution_status": "completed", "uncertainty": uncertainty}


def investigation(iid, run, findings):
    out = {s: {"answers": [], "findings": [f for f in findings if f["signal_type"] == s], "status": "completed"}
           for s in ("logs", "metrics", "traces")}
    return {"incident_id": iid, "run_id": run, "logs_output": out["logs"], "metrics_output": out["metrics"],
            "traces_output": out["traces"], "missing_evidence": [], "status": "completed",
            "investigation_plan_id": "PLAN-" + sha(run, 8), "parent_run_id": None}


def candidate(iid, service, description, links, uncertainty=None):
    return {"candidate_id": "CAUSE-" + sha("|".join([iid, service, *sorted(l["evidence_id"] for l in links)]), 16),
            "description": f"{service}: {description}", "evidence_links": links, "uncertainty": uncertainty}


def correlation(iid, run, candidates, leading=True, status="completed", missing=(), follow_ups=()):
    return {"incident_id": iid, "run_id": run, "root_cause_candidates": candidates,
            "leading_candidate_id": candidates[0]["candidate_id"] if leading and candidates else None,
            "missing_evidence": list(missing), "status": status, "follow_up_requests": list(follow_ups)}


def option(rid, action, action_type, links, validation, rationale, caveat=None):
    return {"recommendation_id": rid, "action": action, "action_type": action_type, "evidence_links": links,
            "risk_or_caveat": caveat, "validation": validation, "rationale": rationale}


def recommendations(iid, corr_run, options, ranked, status="completed"):
    return {"incident_id": iid, "correlation_run_id": corr_run, "recommendation_options": options,
            "ranked_recommendation_ids": ranked, "top_recommendation_id": ranked[0] if ranked else None,
            "status": status}


def f1_from_bundle(doc: dict, resolved: bool) -> dict:
    alerts = []
    for aid in sorted(doc["alerts"]):
        a = doc["alerts"][aid]
        alert = {"alert_id": aid, "signal_type": "metrics", "service": a["service"],
                 "severity": SEVERITY[a["severity"]], "timestamp": a["detected_at"],
                 "description": f"{a['title']} on {a['service']} exceeded {a['threshold']:g} {a['unit']} "
                                f"(peak {a['peak_value']:.3f})",
                 "evidence_ids": [], "first_seen": a["detected_at"]}
        if resolved and a.get("resolved_at"):
            alert["last_seen"] = a["resolved_at"]
        alerts.append(alert)
    incident = {"incident_id": doc["id"], "title": doc["summary"], "alerts": sorted(doc["alerts"]),
                "start_time": doc["first_observed_at"], "end_time": doc["resolved_at"] if resolved else None,
                "affected_services": doc["affected_services"], "severity": SEVERITY[doc["severity"]]}
    return {"incident": incident, "alerts": alerts}


def payments(args) -> dict:
    doc = json.loads(Path(args.f1_incident).read_text(encoding="utf-8"))
    inv = json.loads(Path(args.f2_investigation).read_text(encoding="utf-8-sig"))
    corr = json.loads(Path(args.f3_correlation).read_text(encoding="utf-8"))
    failed = json.loads(Path(args.f3_failed).read_text(encoding="utf-8"))
    iid = doc["id"]
    supports = [l for l in corr["root_cause_candidates"][0]["evidence_links"] if l["relation"] == "supports"]
    recs = recommendations(iid, corr["run_id"], [
        option("REC-" + sha(iid + "restart", 8), "Restart payments-service and confirm POST /payments/charge "
               "returns 2xx before resuming traffic", "remediation", supports, "conditional",
               "Logs, metrics and traces localise the 503 responses to payments-service.",
               "A restart clears the symptom but not an upstream payment-provider outage."),
        option("REC-" + sha(iid + "provider", 8), "Check the payment provider's status for the incident window",
               "investigation", supports[:1], "supported",
               "PaymentServiceUnavailable is raised inside payments-service; its dependency was not observed."),
        option("REC-" + sha(iid + "verify", 8), "Verify that orders-service and api-gateway 5xx ratios fall "
               "below their thresholds", "verification", [], "supported",
               "Downstream alerts should clear once payments-service recovers."),
    ], [])
    recs["ranked_recommendation_ids"] = [o["recommendation_id"] for o in recs["recommendation_options"]]
    recs["top_recommendation_id"] = recs["ranked_recommendation_ids"][0]

    sid = corr["root_cause_candidates"][0]["evidence_links"][0]["evidence_id"].split("/")[1]
    tentative = candidate(iid, "payments-service", "Payments-service returned HTTP 503 for POST /payments/charge.",
                          [l for l in supports if "/logs#" in l["evidence_id"]],
                          uncertainty="Only log evidence supports this; no second signal type confirms it.")
    inconclusive_run = "RUN-" + sha(iid + "inconclusive", 16)
    inconclusive = correlation(iid, inconclusive_run, [tentative], leading=False, status="inconclusive", missing=[
        {"required": "Failed call path from api-gateway to payments-service", "requested_from": "traces",
         "reason": "No retained trace links the gateway errors to payments-service.", "status": "unavailable"}],
        follow_ups=[{"request_id": "REQ-" + sha(inconclusive_run, 16), "incident_id": iid, "origin": "correlation",
                     "target_agents": ["traces"], "objectives": ["Failed call path from api-gateway to payments-service"],
                     "evidence_needed": ["Failed call path from api-gateway to payments-service"],
                     "signal_objectives": {}, "source_run_id": inv["run_id"], "review_cycle_id": None,
                     "status": "rejected"}])
    empty = recommendations(iid, inconclusive_run, [
        option("REC-" + sha(iid + "traces", 8), "Collect traces for failed checkout requests during a "
               "controlled retry", "verification", [], "supported", "The missing call path blocks a diagnosis."),
        option("REC-" + sha(iid + "dependency", 8), "Review payments-service dependency health for the window",
               "investigation", [], "supported", "No cause is established; this narrows the search."),
    ], [], status="inconclusive")

    contradiction = json.loads(json.dumps(corr))
    contradiction["run_id"] = "RUN-" + sha(iid + "contradiction", 16)
    contradiction["root_cause_candidates"][0]["evidence_links"] += [
        link(f"{iid}/{sid}/metrics#M3", relation="contradicts", support="contradicted"),
        link(f"{iid}/{sid}/traces#T9", relation="supports", support="unverified", valid="unknown", relevance="unknown")]
    contradiction["root_cause_candidates"][0]["uncertainty"] = (
        "One metrics observation contradicts the cause and one trace reference could not be resolved.")

    v2_inv = json.loads(json.dumps(inv))
    v2_inv.update(run_id="RUN-" + sha(inv["run_id"] + "revision-1", 8), parent_run_id=inv["run_id"],
                  investigation_plan_id="PLAN-" + sha(inv["run_id"] + "revision-1", 8))
    v2_corr = json.loads(json.dumps(corr))
    v2_corr["run_id"] = "RUN-" + sha(corr["run_id"] + "revision-1", 16)
    v2_recs = json.loads(json.dumps(recs))
    v2_recs["correlation_run_id"] = v2_corr["run_id"]
    return {
        "open": f1_from_bundle(doc, resolved=False), "resolved": f1_from_bundle(doc, resolved=True),
        "investigation": inv, "correlation": corr, "inconclusive": inconclusive, "contradiction": contradiction,
        "failed": failed, "recommendations": recs, "recommendations_empty": empty,
        "v2": (v2_inv, v2_corr, v2_recs),
    }


def synthetic(name: str, service: str, title: str, alerts: list, start: str, end: str,
              findings_spec: list, cause: str, options_spec: list) -> dict:
    iid = "INC-" + sha(name, 20)
    sid = "r1-" + hashlib.sha256((name + "snapshot").encode()).hexdigest()
    run = "RUN-" + sha(name + "f2", 8)
    f1 = {"incident": {"incident_id": iid, "title": title, "alerts": sorted(a["alert_id"] for a in alerts),
                       "start_time": start, "end_time": None, "affected_services": sorted({a["service"] for a in alerts}),
                       "severity": max((a["severity"] for a in alerts), key=["low", "medium", "high", "critical"].index)},
          "alerts": sorted(alerts, key=lambda a: a["alert_id"])}
    finds = [finding(iid, sid, *spec) for spec in findings_spec]
    inv = investigation(iid, run, finds)
    supports = [f["evidence_links"][0] for f in finds if f["claim_kind"] == "observation" and f["service"] == service]
    corr = correlation(iid, "RUN-" + sha(name + "f3", 16), [candidate(iid, service, cause, supports)])
    opts = [option("REC-" + sha(name + str(i), 8), action, kind, supports if kind == "remediation" else supports[:1],
                   validation, rationale, caveat) for i, (action, kind, validation, rationale, caveat)
            in enumerate(options_spec)]
    recs = recommendations(iid, corr["run_id"], opts, [o["recommendation_id"] for o in opts])
    return {"f1": f1, "f2": inv, "f3": corr, "f4": recs}


def alert(seed, signal, service, severity, ts, description):
    return {"alert_id": sha(seed, 20), "signal_type": signal, "service": service, "severity": severity,
            "timestamp": ts, "description": description, "evidence_ids": [], "first_seen": ts}


def db_exhaustion() -> dict:
    s = "orders-service"
    return synthetic(
        "db_connection_exhaustion-demo", s, "Observed database symptoms in orders-service",
        [alert("db-pool-pressure", "metrics", s, "high", "2026-11-24T09:12:20Z",
               "Database pool utilization on orders-service exceeded 0.9 ratio (peak 1.000)"),
         alert("db-pool-timeouts", "metrics", s, "high", "2026-11-24T09:12:30Z",
               "Database acquire timeouts on orders-service exceeded 2 count_per_window (peak 14)"),
         alert("db-orders-5xx", "metrics", s, "medium", "2026-11-24T09:12:40Z",
               "HTTP 5xx ratio on orders-service exceeded 0.15 ratio (peak 0.410)"),
         alert("db-gateway-dep", "metrics", "api-gateway", "medium", "2026-11-24T09:13:00Z",
               "Dependency 5xx or transport failure ratio on api-gateway exceeded 0.15 ratio (peak 0.330)")],
        "2026-11-24T09:12:05Z", None,
        [("logs", 1, s, "orders-service logged 42 'db_pool_timeout' failure records (PoolTimeoutError) over 60 s "
                        "in the retained sample", "P1", "observation", "db_pool_timeout"),
         ("metrics", 1, s, "orders-service breached pool_pressure (database) with trigger value 1.0 vs threshold "
                           "0.9 ratio, trend: worsening", "M1", "observation", "pool_pressure"),
         ("metrics", 2, s, "orders-service breached pool_timeouts (database) with trigger value 14 vs threshold 2 "
                           "count_per_window, trend: worsening", "M2", "observation", "pool_timeouts"),
         ("traces", 1, s, "orders-service observed 9 'failed_span' events for 'db.acquire', p95 5000 ms "
                          "(deepest failure origin) in the retained traces", "T1", "observation", "db.acquire"),
         ("logs", 2, s, "Connection acquisitions waited for the full timeout while every connection was busy.",
          "P1", "inference", None, "confidence: medium")],
        "The database connection pool reached capacity and connection acquisitions timed out.",
        [("Raise the orders-service pool maximum from 10 to 20 connections and restart", "remediation",
          "conditional", "Occupancy reached 100% while acquisitions timed out.",
          "Check PostgreSQL max_connections headroom before raising the pool size."),
         ("Identify long-held connections in pg_stat_activity for the incident window", "investigation",
          "supported", "Saturation may come from slow queries rather than load.", None),
         ("Confirm pool occupancy stays below 70% for 15 minutes after the change", "verification",
          "supported", "Shows whether the remediation removed the pressure.", None)])


def bad_deployment() -> dict:
    s = "orders-service"
    return synthetic(
        "bad_deployment-demo", s, "Observed regression symptoms in orders-service",
        [alert("bd-persistence", "logs", s, "high", "2026-11-24T11:05:30Z",
               "order_persistence_failed on orders-service exceeded 2 events_per_window (peak 18)"),
         alert("bd-exception", "logs", s, "medium", "2026-11-24T11:05:40Z",
               "unexpected_exception on orders-service exceeded 3 events_per_window (peak 9)"),
         alert("bd-orders-5xx", "metrics", s, "high", "2026-11-24T11:05:50Z",
               "HTTP 5xx ratio on orders-service exceeded 0.15 ratio (peak 0.520)")],
        "2026-11-24T11:05:12Z", None,
        [("logs", 1, s, "orders-service recorded release_changed to 2.4.1 at 2026-11-24T11:04:58Z; first retained "
                        "OrderStateWriteError on that release at 2026-11-24T11:05:09Z.", "P1", "observation",
          "release_changed"),
         ("logs", 2, s, "orders-service logged 18 'order_persistence_failed' failure records (OrderStateWriteError) "
                        "over 60 s in the retained sample", "P2", "observation", "order_persistence_failed"),
         ("metrics", 1, s, "orders-service breached persistence_errors (regression) with trigger value 18 vs "
                           "threshold 2 events_per_window, trend: worsening", "M1", "observation", "persistence_errors"),
         ("traces", 1, s, "orders-service observed 12 'failed_span' events for 'POST /orders', HTTP 500 "
                          "(deepest failure origin) in the retained traces", "T1", "observation", "POST /orders")],
        "Release 2.4.1 introduced failing order-state writes (OrderStateWriteError).",
        [("Roll orders-service back to release 2.4.0", "remediation", "supported",
          "Failures begin on 2.4.1 and are confined to orders-service.",
          "Rolling back also removes any fixes shipped in 2.4.1."),
         ("Diff the 2.4.1 order-state write path against 2.4.0", "investigation", "supported",
          "Locates the regression for a forward fix.", None),
         ("Confirm order_persistence_failed returns to zero after the rollback", "verification", "supported",
          "Shows the rollback removed the regression.", None)])


def bundle(f1, inv, corr, recs) -> dict:
    return {"incident": f1["incident"], "alerts": f1["alerts"], "investigation": inv, "correlation": corr,
            "recommendations": recs}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("--f1-incident", "--f2-investigation", "--f3-correlation", "--f3-failed"):
        ap.add_argument(name, required=True)
    p = payments(ap.parse_args())
    db, bd = db_exhaustion(), bad_deployment()
    v2_inv, v2_corr, v2_recs = p["v2"]
    files = {
        ("f1", "incident_payments_open"): (F1Output, p["open"]),
        ("f1", "incident_payments_resolved"): (F1Output, p["resolved"]),
        ("f1", "incident_db_exhaustion"): (F1Output, db["f1"]),
        ("f1", "incident_bad_deployment"): (F1Output, bd["f1"]),
        ("f2", "investigation_payments"): (InvestigationResult, p["investigation"]),
        ("f2", "investigation_partial"): (InvestigationResult, json.loads(
            (REPO / "investigation/fixtures/investigation_result_partial.json").read_text(encoding="utf-8"))),
        ("f2", "investigation_db_exhaustion"): (InvestigationResult, db["f2"]),
        ("f2", "investigation_bad_deployment"): (InvestigationResult, bd["f2"]),
        ("f3", "correlation_payments_completed"): (CorrelationResultIn, p["correlation"]),
        ("f3", "correlation_inconclusive"): (CorrelationResultIn, p["inconclusive"]),
        ("f3", "correlation_contradiction"): (CorrelationResultIn, p["contradiction"]),
        ("f3", "correlation_failed"): (CorrelationResultIn, p["failed"]),
        ("f3", "correlation_db_exhaustion"): (CorrelationResultIn, db["f3"]),
        ("f3", "correlation_bad_deployment"): (CorrelationResultIn, bd["f3"]),
        ("f4", "recommendations_payments"): (RecommendationResultIn, p["recommendations"]),
        ("f4", "recommendations_empty"): (RecommendationResultIn, p["recommendations_empty"]),
        ("f4", "recommendations_db_exhaustion"): (RecommendationResultIn, db["f4"]),
        ("f4", "recommendations_bad_deployment"): (RecommendationResultIn, bd["f4"]),
        ("bundles", "payments_v1"): (ReportInputs, bundle(p["open"], p["investigation"], p["correlation"],
                                                          p["recommendations"])),
        ("bundles", "payments_v2_revised"): (ReportInputs, bundle(p["resolved"], v2_inv, v2_corr, v2_recs)),
        ("bundles", "db_exhaustion"): (ReportInputs, bundle(db["f1"], db["f2"], db["f3"], db["f4"])),
        ("bundles", "bad_deployment"): (ReportInputs, bundle(bd["f1"], bd["f2"], bd["f3"], bd["f4"])),
        ("bundles", "inconclusive"): (ReportInputs, bundle(p["open"], p["investigation"], p["inconclusive"],
                                                           p["recommendations_empty"])),
        ("bundles", "missing_f4"): (ReportInputs, bundle(p["open"], p["investigation"], p["correlation"], None)),
        ("bundles", "failed_f3"): (ReportInputs, bundle(p["open"], p["investigation"], p["failed"], None)),
    }
    for (group, name), (model, value) in files.items():
        model.model_validate(value)  # never write an invalid fixture
        write(group, name, value)
    print(f"wrote {len(files)} fixtures under {OUT}")


if __name__ == "__main__":
    main()
