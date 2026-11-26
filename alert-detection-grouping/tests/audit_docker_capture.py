"""Check persisted live captures; run after run_docker_validation.py completes."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    run = json.loads((ROOT / "validation/current-run.json").read_text())
    output = Path(run["output"])
    results = json.loads((output / "results.json").read_text())
    assert all(r["passed"] for r in results), "A live execution failed"
    checks = []
    for name, rule, services in (
        ("payments-503", "payment_failures", {"payments-service", "orders-service", "api-gateway"}),
        ("payments-402", "payment_failures", {"payments-service"}),
        ("pool", "pool_timeouts", {"orders-service", "api-gateway"}),
        ("deployment", "persistence_errors", {"orders-service", "api-gateway"}),
    ):
        result = next(r for r in reversed(results) if r["case"].removesuffix("-resumed") == name)
        phase = result["case"]
        assert len(result["incidents"]) == 1, f"Unexpected split: {name}"
        ident = result["incidents"][0]["id"]
        during = json.loads((output / (phase + "-during") / (ident + ".json")).read_text())
        recovered = json.loads((output / (phase + "-recovered") / (ident + ".json")).read_text())
        assert during["origin"] == "live" and during["status"] == "open"
        assert set(during["affected_services"]) == services
        assert recovered["status"] == "resolved" and recovered["revision"] > during["revision"]
        assert all(a["status"] == "resolved" for a in recovered["alerts"].values())
        assert recovered["root_cause"]["status"] == "not_determined"
        assert recovered["handoff"]["ready_for_investigation"]
        assert not during["data_gaps"] and not recovered["data_gaps"]
        assert during["evidence"]["logs"] and during["evidence"]["traces"]
        assert rule in {a["rule"] for a in during["alerts"].values()}
        assert all(a["trigger_evidence"]["evidence"]["query"] for a in during["alerts"].values())
        if len(services) > 1:
            linked = [r for r in during["relationships"] if r["kind"] == "failed_dependency"]
            assert linked and all(r["witnesses"] for r in linked)
        if name == "payments-402":
            assert {a["rule"] for a in during["alerts"].values()} == {"payment_failures"}
        checks.append({"case": name, "passed": True, "incident_id": ident,
                       "detection_delay_seconds": result["detection_delay_seconds"],
                       "during_revision": during["revision"], "resolved_revision": recovered["revision"],
                       "resolved_at": recovered["resolved_at"], "phase": phase})
    outage = json.loads((output / "prometheus-outage/summary.json").read_text())
    assert outage["source_health"]["prometheus"] != "ok"
    assert any(i["status"] == "open" and i["services"] == ["observability:prometheus"]
               for i in outage["incidents"])
    before = json.loads((output / "before-detector-restart/summary.json").read_text())
    after = json.loads((output / "after-detector-restart/summary.json").read_text())
    assert all(i["status"] == "resolved" for i in after["incidents"])
    assert {i["id"] for i in before["incidents"]} == {i["id"] for i in after["incidents"]}
    audit = {"run_id": run["run_id"], "passed": True, "cases": checks,
             "source_outage_recovery": True, "restart_preserved_incidents": True}
    (output / "capture-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
