"""Read pinned F1 evidence for one incident, from the opening through the selected revision."""
from datetime import datetime
import json

from observa_alerts.evidence import checked_file, load_manifest, read_evidence
from observa_alerts.report import SNAPSHOT_ID, incident_root

from .state import AlertPeak, Dependency, IncidentHistory, Observation, OpeningAlert


def records(reports, iid, sid, ref):
    cursor = 0
    while cursor is not None:
        page = read_evidence(reports, iid, sid, ref, cursor=cursor, limit=100, max_bytes=65536)
        if page["error"]:
            raise ValueError(f"Cannot read {iid}/{sid}/{ref} at record {cursor}")
        for index, item in enumerate(page["items"], cursor):
            yield f"{iid}/{sid}/{ref}#{index}", item
        cursor = page["next_cursor"]


def release_findings(entries, iid, sid, services):
    changes = {}
    errors = {}
    for ref, row in entries:
        service = row.get("service.name")
        version = row.get("service.version")
        when = row.get("timestamp")
        if service not in services or not version or not when:
            continue
        deployment = (service, version, row.get("deployment.id"))
        instant = datetime.fromisoformat(when.replace("Z", "+00:00"))
        if row.get("event") == "release_changed":
            changes.setdefault(deployment, []).append((instant, when, ref))
        elif row.get("level") == "ERROR" and row.get("error.type"):
            first = errors.get((deployment, row["error.type"]))
            if first is None or instant < first[0]:
                errors[deployment, row["error.type"]] = (instant, when, ref)
    for (deployment, error), (instant, when, error_ref) in errors.items():
        preceding = [r for r in changes.get(deployment, []) if r[0] <= instant]
        if preceding:
            _, changed_at, change_ref = max(preceding)
            service, version, _ = deployment
            yield (deployment, error), Observation(
                signal="logs", service=service, snapshot_id=sid, indicator="release_changed",
                finding_id=f"{iid}:release:{sid}:{error_ref.rsplit('#', 1)[1]}",
                claim=f"{service} recorded release_changed to {version} at {changed_at}; "
                      f"first retained {error} on that release at {when}.",
                evidence_ids=[change_ref, error_ref])


def failed_trace_edges(entries, trace_id, services):
    spans = {span["span_id"]: (ref, span) for ref, span in entries if span.get("span_id")}
    for child_ref, child in spans.values():
        callee = child.get("service")
        if callee not in services or not child.get("error"):
            continue
        for link in child.get("references", []):
            parent = spans.get(link.get("spanID"))
            if link.get("refType") != "CHILD_OF" or link.get("traceID") != trace_id or not parent:
                continue
            parent_ref, caller_span = parent
            caller = caller_span.get("service")
            if caller != callee and caller in services and caller_span.get("error"):
                yield Dependency(caller=caller, callee=callee, signal="traces",
                                 evidence_id=child_ref, parent_evidence_id=parent_ref)


def load_history(reports, iid, selected):
    """Return the incident history and the pinned F2 inputs for onset and greatest concurrent impact."""
    root = incident_root(reports, iid)
    if not SNAPSHOT_ID.fullmatch(selected):
        raise ValueError("Invalid selected snapshot")
    last_revision = int(selected.split("-")[0][1:])
    # Verify the selected input first. A newer F1 publication cannot move this run forward.
    load_manifest(reports, iid, selected)
    selected_doc = json.loads(checked_file(root / "snapshots" / selected / "incident.json"))
    final = selected_doc["status"] == "resolved"
    snapshots = [(last_revision, selected)]
    for path in (root / "snapshots").iterdir():
        if SNAPSHOT_ID.fullmatch(path.name):
            revision = int(path.name.split("-")[0][1:])
            if revision < last_revision and (path / "incident.json").is_file():
                snapshots.append((revision, path.name))
    peaks, dependencies, releases = {}, {}, {}
    seen_blobs = set()
    first = impact = None
    impact_score = (-1, -1)
    for _, sid in sorted(snapshots):
        _, manifest = load_manifest(reports, iid, sid)
        doc = json.loads(checked_file(root / "snapshots" / sid / "incident.json"))
        if first is None:
            first = doc
        active = [a for a in doc["alerts"].values() if a["data_quality"] == "bad"]
        score = (len({a["service"] for a in active}), len(active))
        if score > impact_score:
            impact_score, impact = score, doc
        for aid, alert in doc["alerts"].items():
            value = alert.get("peak_value")
            if value is not None and (aid not in peaks or value > peaks[aid].value):
                peaks[aid] = AlertPeak(service=alert["service"], rule=alert["rule"], unit=alert["unit"],
                                       value=value, recorded_at=doc["updated_at"],
                                       evidence_id=f"{iid}/{sid}/incident.json#/alerts/{aid}/peak_value")
        trace_refs = [key for key in manifest["refs"] if key.startswith("trace-")]
        for ref in ["relationships", "logs", *trace_refs]:
            blob = manifest["refs"].get(ref)
            if not blob or (ref, blob["sha256"]) in seen_blobs:
                continue
            seen_blobs.add((ref, blob["sha256"]))
            entries = records(reports, iid, sid, ref)
            if ref == "logs":
                for key, finding in release_findings(entries, iid, sid, selected_doc["affected_services"]):
                    releases.setdefault(key, finding)
            elif ref.startswith("trace-"):
                for edge in failed_trace_edges(entries, blob["metadata"]["trace_id"], selected_doc["affected_services"]):
                    dependencies.setdefault((edge.caller, edge.callee), edge)
            else:
                for pointer, edge in entries:
                    direction = edge.get("direction", {})
                    caller, callee = direction.get("upstream"), direction.get("downstream")
                    if edge.get("kind") == "failed_dependency" and caller and callee and any(
                        w.get("service.name") == caller and w.get("dependency") == callee
                        and w.get("event") == "dependency_request_failed" for w in edge.get("witnesses", [])
                    ):
                        dependencies.setdefault((caller, callee), Dependency(
                            caller=caller, callee=callee, signal="logs", evidence_id=pointer))
    anchor = first["alerts"].get(first.get("anchor_alert_id"))
    opening = OpeningAlert(**anchor) if anchor else None
    history = IncidentHistory(opening_alert=opening, phase="final" if final else "initial", first_snapshot=first["snapshot_id"],
                              snapshot_id=selected, first_observed_at=selected_doc["first_observed_at"],
                              resolved_at=selected_doc.get("resolved_at"), peaks=list(peaks.values()),
                              affected_services=selected_doc["affected_services"],
                              dependencies=list(dependencies.values()), release_findings=list(releases.values()))
    return history, [first, impact] if impact["snapshot_id"] != first["snapshot_id"] else [first]
