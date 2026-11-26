"""Compact v2 handoff and immutable, content-addressed local evidence.

The detector's internal state remains v1. Only the exported delivery contract changes.
One writer per reports directory; old snapshots/blobs are retained deliberately.
"""
import hashlib
import json
from pathlib import Path
import re

INCIDENT_ID = re.compile(r"INC-[0-9a-f]{20}\Z")
SNAPSHOT_ID = re.compile(r"r[1-9][0-9]*-[0-9a-f]{64}\Z")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def write_atomic(target, body):
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(body, encoding="utf-8", newline="\n")
    temp.replace(target)


def blob(root, category, records, metadata=None):
    body = "".join(encoded(row) + "\n" for row in records)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    relative = f"evidence/.objects/{category}-{digest}.jsonl"
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evidence path escapes incident directory")
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError("Existing evidence failed checksum verification")
    else:
        write_atomic(target, body)
    return {"kind": category, "path": relative, "sha256": digest,
            "format": "jsonl", "record_count": len(records), "metadata": metadata or {}}


def incident_root(directory, ident):
    if not INCIDENT_ID.fullmatch(ident):
        raise ValueError("Invalid incident ID")
    base = Path(directory).resolve()
    root = (base / ident).resolve()
    if not root.is_relative_to(base):
        raise ValueError("Incident path escapes reports directory")
    return root


def make_bundle(i, root, snapshot_id):
    refs = {}
    refs["logs"] = blob(root, "logs", i["evidence"]["logs"],
                        {"coverage": "retained sanitized samples, not all source logs"})
    metrics = []
    for a in i["alerts"].values():
        for phase, obs in (("trigger", a["trigger_evidence"]), ("latest", a)):
            if obs["evidence"].get("source") == "prometheus":
                metrics.append({"alert_id": a["id"], "service": a["service"], "rule": a["rule"],
                                "phase": phase, "value": obs["value"], "threshold": obs["threshold"],
                                "unit": obs["unit"], "evidence": obs["evidence"]})
    refs["metrics"] = blob(root, "metrics", metrics,
                           {"coverage": "observed alert values and original queries, not complete time series"})
    catalog = []
    for trace in i["evidence"]["traces"]:
        descriptor = blob(root, "traces", trace["spans"],
                          {k: v for k, v in trace.items() if k != "spans"})
        # Include trace identity as well as content so identical spans cannot conflate traces.
        ref_id = "trace-" + hashlib.sha256(encoded(descriptor).encode()).hexdigest()[:24]
        refs[ref_id] = descriptor
        catalog.append({"trace_id": trace["trace_id"], "ref_id": ref_id,
                        "span_count": len(trace["spans"]),
                        "services": sorted({s["service"] for s in trace["spans"]}),
                        "failed_span_count": sum(bool(s.get("error") or (s.get("http_status_code") or 0) >= 500)
                                                 for s in trace["spans"])})
    refs["traces"] = blob(root, "trace-catalog", catalog,
                          {"coverage": "retained traces only; read a trace ref to page through its spans"})
    refs["relationships"] = blob(root, "relationships", i["relationships"])
    refs["timeline"] = blob(root, "timeline", i["timeline"])
    # Preserve all original fields for reproducibility, without embedding them in agent input.
    metadata = {k: v for k, v in i.items() if k not in ("alerts", "evidence", "relationships", "timeline")}
    details = [{"kind": "incident_metadata", "value": metadata}]
    details += [{"kind": "alert", "alert_id": aid, "value": a} for aid, a in i["alerts"].items()]
    refs["details"] = blob(root, "details", details)
    fields = ("id", "revision", "origin", "environment", "summary", "status", "severity",
              "first_observed_at", "created_at", "updated_at", "last_observed_at", "resolved_at",
              "affected_services", "anchor_alert_id", "anchor", "source_health", "data_gaps", "root_cause", "handoff")
    compact = {k: i[k] for k in fields if k in i}
    compact.update(schema_version="2.0", snapshot_id=snapshot_id)
    alert_fields = ("id", "incident_id", "rule", "title", "service", "family", "severity", "status",
                    "value", "threshold", "unit", "data_quality", "detected_at", "resolved_at", "peak_value")
    compact["alerts"] = {
        aid: {**{k: a[k] for k in alert_fields if k in a},
              "trigger_value": a["trigger_evidence"]["value"], "detail_ref": "details"}
        for aid, a in i["alerts"].items()}
    compact["relationships"] = [
        {**{k: v for k, v in r.items() if k not in ("witnesses", "trace_ids")}, "evidence_ref": "relationships"}
        for r in i["relationships"]]
    compact["evidence_refs"] = {key: {"ref_id": key, "record_count": refs[key]["record_count"]}
                                for key in ("logs", "metrics", "traces", "relationships", "timeline", "details")}
    compact["manifest"] = f"snapshots/{snapshot_id}/manifest.json"
    compact["evidence_note"] = "Local, bounded saved evidence. Pin snapshot_id when reading refs; missing data is not recovery."
    manifest = {"schema_version": "2.0", "incident_id": i["id"], "snapshot_id": snapshot_id,
                "query_window": i["handoff"]["query_window"], "origin": i["origin"], "refs": refs}
    return compact, manifest


def markdown(i):
    lines = ["# " + i["id"], "", "**Status:** " + i["status"] + " | **Severity:** " + i["severity"],
             "**Origin:** " + i["origin"] + " | **Environment:** " + i["environment"],
             "**Revision:** " + str(i["revision"]), "", i["summary"], "",
             "First observed (UTC): " + i["first_observed_at"], "Updated (UTC): " + i["updated_at"],
             "Resolved (UTC): " + i.get("resolved_at", "not yet confirmed"), "",
             "Affected services: " + ", ".join(i["affected_services"]), "",
             f"All {len(i['alerts'])} alerts below belong to incident **{i['id']}**.", "",
             "| Alert ID | Rule | Service | Status | Latest value | Threshold | Unit | Quality |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for a in i["alerts"].values():
        lines.append("| " + " | ".join(str(a.get(k)) for k in
                     ("id", "rule", "service", "status", "value", "threshold", "unit", "data_quality")) + " |")
    lines += ["", "## Why these alerts are grouped", ""]
    for r in i["relationships"]:
        member = i["alerts"][r["alert_id"]]
        other = i["alerts"][r["related_alert_id"]]
        lines.append(f"- {member['service']} ({member['id']}) linked to {other['service']} ({other['id']}): "
                     f"{r['kind']}; confidence: **{r['confidence']}**. {r['explanation']}")
        if r.get("direction"):
            lines.append(f"  Call direction: {r['direction']['upstream']} → {r['direction']['downstream']}.")
    if not i["relationships"]:
        lines.append("Single alert; no cross-alert relationship asserted.")
    lines += ["", "## Evidence and handoff", "",
              "Send [incident.json](incident.json) to the agent first. Full evidence is stored separately.",
              f"Pinned input: [this snapshot](snapshots/{i['snapshot_id']}/incident.json).",
              f"Evidence map: [manifest]({i['manifest']}).", "",
              "| Evidence ref | Saved records |", "| --- | --- |"]
    for key, ref in i["evidence_refs"].items():
        lines.append(f"| {key} | {ref['record_count']} |")
    lines += ["", "Metrics are observed alert values/queries, not complete time series. Logs and traces are bounded samples.",
              "**Root cause: not determined.** Grouping is not proof of causation.", "", "## Data gaps", ""]
    lines += ["- " + g for g in i["data_gaps"]] or ["No source gaps reported in this saved snapshot."]
    lines += ["", "## Next investigation steps", ""] + ["- " + s for s in i["handoff"]["next_steps"]]
    return "\n".join(lines) + "\n"


def export(incidents, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    exported = []
    for i in incidents:
        root = incident_root(directory, i["id"])
        digest = hashlib.sha256(encoded(i).encode()).hexdigest()
        snapshot_id = f"r{i['revision']}-{digest}"
        if not SNAPSHOT_ID.fullmatch(snapshot_id):
            raise ValueError("Invalid incident revision")
        snapshot = root / "snapshots" / snapshot_id
        ready = snapshot / "incident.json"
        if ready.exists():
            compact = json.loads(ready.read_text(encoding="utf-8"))
            if not (snapshot / "manifest.json").is_file():
                raise ValueError("Incomplete saved snapshot")
        else:
            compact, manifest = make_bundle(i, root, snapshot_id)
            # Evidence first; immutable snapshot commit last; current input published afterwards.
            manifest_body = json.dumps(manifest, indent=2, ensure_ascii=False)
            compact["manifest_sha256"] = hashlib.sha256(manifest_body.encode()).hexdigest()
            write_atomic(snapshot / "manifest.json", manifest_body)
            write_atomic(ready, json.dumps(compact, indent=2, ensure_ascii=False, allow_nan=False))
        current = root / "incident.json"
        views = ("summary.md", "evidence/logs.jsonl", "evidence/metrics.json", "evidence/traces/index.json")
        if current.exists() and json.loads(current.read_text(encoding="utf-8")) == compact and all(
                (root / name).is_file() for name in views):
            exported.append(compact)
            continue
        # Human-readable latest views. Agents use pinned manifest refs, never these mutable views.
        write_atomic(root / "evidence" / "logs.jsonl", "".join(encoded(row) + "\n" for row in i["evidence"]["logs"]))
        manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        metric_rows = [json.loads(line) for line in (root / manifest["refs"]["metrics"]["path"]).read_text(encoding="utf-8").splitlines()]
        write_atomic(root / "evidence" / "metrics.json", json.dumps({
            "snapshot_id": snapshot_id, "query_window": i["handoff"]["query_window"],
            "coverage": "Observed alert values and queries; not complete time series", "observations": metric_rows}, indent=2))
        trace_index = []
        for trace in i["evidence"]["traces"]:
            trace_hash = hashlib.sha256(encoded(trace).encode()).hexdigest()
            if not re.fullmatch(r"[0-9a-fA-F]{1,64}", trace["trace_id"]):
                raise ValueError("Invalid trace ID")
            name = f"trace-{trace['trace_id']}-{trace_hash[:12]}.json"
            target = root / "evidence" / "traces" / name
            if not target.exists():
                write_atomic(target, json.dumps(trace, indent=2, ensure_ascii=False))
            trace_index.append({"trace_id": trace["trace_id"], "file": name})
        write_atomic(root / "evidence" / "traces" / "index.json", json.dumps({
            "snapshot_id": snapshot_id, "traces": trace_index}, indent=2))
        write_atomic(root / "summary.md", markdown(compact))
        write_atomic(root / "incident.json", json.dumps(compact, indent=2, ensure_ascii=False, allow_nan=False))
        exported.append(compact)
    write_index(exported, directory)
    return exported


def write_index(incidents, directory):
    directory = Path(directory)
    ordered = sorted(incidents, key=lambda i: (i["created_at"], i["id"]), reverse=True)
    index = [{"id": i["id"], "status": i["status"], "severity": i["severity"], "revision": i["revision"],
              "snapshot_id": i["snapshot_id"], "origin": i["origin"], "updated_at": i["updated_at"],
              "affected_services": i["affected_services"], "alert_count": len(i["alerts"]),
              "alert_ids": sorted(i["alerts"]), "json_file": i["id"] + "/incident.json",
              "report_file": i["id"] + "/summary.md"} for i in ordered]
    write_atomic(directory / "index.json", json.dumps(index, indent=2))
    lines = ["# Incidents", "", "Open a summary; send only incident.json to the agent initially. Times are UTC.", "",
             "| Incident | Status | Severity | Alerts | Services | Origin | Updated (UTC) | Agent input |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in index:
        lines.append(f"| [{row['id']}]({row['report_file']}) | {row['status']} | {row['severity']} | "
                     f"{row['alert_count']} | {', '.join(row['affected_services'])} | {row['origin']} | "
                     f"{row['updated_at']} | [JSON]({row['json_file']}) |")
    if not index:
        lines += ["", "No incidents have been created yet. This alone does not confirm source health."]
    write_atomic(directory / "index.md", "\n".join(lines) + "\n")
