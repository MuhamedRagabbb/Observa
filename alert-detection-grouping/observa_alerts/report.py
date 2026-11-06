import json
from pathlib import Path


def markdown(i):
    lines = ["# " + i["id"], "", "**Evidence origin:** " + i["origin"],
             "**Status:** " + i["status"] + " | **Revision:** " + str(i["revision"]),
             "**Environment:** " + i["environment"], "**Severity:** " + i["severity"],
             "**Summary:** " + i["summary"], "",
             "First observed: " + i["first_observed_at"], "Incident created: " + i["created_at"],
             "Last observed abnormal: " + i["last_observed_at"], "Updated: " + i["updated_at"],
             "Resolved: " + i.get("resolved_at", "not yet confirmed"), "",
             "Affected services: " + ", ".join(i["affected_services"]), "", "## Alerts in this incident", "",
             f"All {len(i['alerts'])} alerts below belong to incident **{i['id']}**.",
             "Anchor alert: " + i["anchor_alert_id"] + " (grouping starting point, not a proven root cause).", "",
             "| Alert ID | Rule | Service | Status | Latest value | Threshold | Unit | Quality |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for a in i["alerts"].values():
        lines.append("| " + " | ".join(str(a.get(k)) for k in (
            "id", "rule", "service", "status", "value", "threshold", "unit", "data_quality")) + " |")
    lines += ["", "## Grouping evidence", ""]
    for r in i["relationships"]:
        related = i["alerts"].get(r.get("related_alert_id"), {})
        member = i["alerts"].get(r["alert_id"], {})
        lines.append("- " + r["alert_id"] + " (" + member.get("service", "unknown") + ") linked to "
                     + r.get("related_alert_id", "incident anchor") + " (" + related.get("service", "unknown")
                     + "): **" + r["kind"] + "**, confidence: **" + r["confidence"] + "**. " + r["explanation"])
        if r.get("direction"):
            lines.append("  Upstream: " + r["direction"]["upstream"] + " → downstream: " + r["direction"]["downstream"])
    if not i["relationships"]:
        lines.append("Single alert; no cross-alert relationship asserted.")
    lines += ["", "## Evidence", "",
              "Companion JSON contains retained sanitized logs, spans, exact queries, values and timestamps.",
              "Raw messages and arbitrary span tags are omitted to reduce accidental personal-data capture.",
              "", "## Data gaps", ""]
    lines += ["- " + g for g in i["data_gaps"]] or ["No latest-poll source error; sampling limits still apply."]
    lines += ["", "## Investigation handoff", "", "**Root cause: not determined.**"]
    lines += ["- " + x for x in i["handoff"]["next_steps"]]
    lines += ["", "Query window: " + json.dumps(i["handoff"]["query_window"]),
              i["handoff"]["evidence_limits"], "", "## Timeline", ""]
    lines += ["- " + e["at"] + " — " + e["event"] + (" — " + e["alert_id"] if e.get("alert_id") else "") for e in i["timeline"]]
    lines += ["", "## Trigger details and reproducible queries", ""]
    for a in i["alerts"].values():
        trigger = a["trigger_evidence"]
        lines += ["### " + a["rule"] + " / " + a["service"], "",
                  "Alert ID: " + a["id"],
                  "First observed value: " + str(trigger["value"]) + "; peak: " + str(a.get("peak_value")),
                  "Detected at: " + a.get("detected_at", "pending"),
                  "Threshold: " + str(a["threshold"]) + " " + a["unit"],
                  "Evidence:", "", "~~~json", json.dumps(trigger["evidence"], indent=2, ensure_ascii=False), "~~~", ""]
    lines += ["## Retained log evidence", "", "~~~json", json.dumps(i["evidence"]["logs"], indent=2, ensure_ascii=False), "~~~",
              "", "## Retained trace evidence", "", "~~~json", json.dumps(i["evidence"]["traces"], indent=2, ensure_ascii=False), "~~~",
              "", "## Candidate hypotheses (unconfirmed)", "", "~~~json",
              json.dumps(i["handoff"]["hypotheses"], indent=2), "~~~"]
    return "\n".join(lines) + "\n"


def write_atomic(target, body):
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(body, encoding="utf-8")
    temp.replace(target)


def export(incidents, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for i in incidents:
        for suffix, body in ((".json", json.dumps(i, indent=2, ensure_ascii=False, allow_nan=False)), (".md", markdown(i))):
            target = directory / (i["id"] + suffix)
            write_atomic(target, body)
    ordered = sorted(incidents, key=lambda i: (i["created_at"], i["id"]), reverse=True)
    index = [{"id": i["id"], "status": i["status"], "revision": i["revision"], "origin": i["origin"],
              "severity": i["severity"], "created_at": i["created_at"], "updated_at": i["updated_at"],
              "affected_services": i["affected_services"], "alert_count": len(i["alerts"]),
              "alert_ids": sorted(i["alerts"]), "json_file": i["id"] + ".json", "report_file": i["id"] + ".md"}
             for i in ordered]
    write_atomic(directory / "index.json", json.dumps(index, indent=2))
    lines = ["# Incidents", "", "Automatically updated. Open an incident report to see its alerts and grouping evidence.",
             "JSON files are the handoff for the next investigation stage. Times are UTC.", "",
             "| Incident | Status | Severity | Alerts | Services | Origin | Updated (UTC) | JSON |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in index:
        lines.append(f"| [{row['id']}]({row['report_file']}) | {row['status']} | {row['severity']} | "
                     f"{row['alert_count']} | {', '.join(row['affected_services'])} | {row['origin']} | "
                     f"{row['updated_at']} | [JSON]({row['json_file']}) |")
    if not index:
        lines += ["", "No incidents have been created yet. This alone does not confirm source health."]
    write_atomic(directory / "index.md", "\n".join(lines) + "\n")
