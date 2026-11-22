"""Watch for new F1 incidents, or process a single incident ID explicitly."""
import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp

from observa_alerts.evidence import load_manifest
from correlation_agent import correlate
from correlation_agent.reporting.report import render_markdown
from correlation_agent.agent.state import CorrelationResult
from correlation_agent.agent.history import load_history
from observa_investigation.agents.live_evidence import LiveEvidence
from observa_investigation.agents.logs import LogsSignalAgent
from observa_investigation.agents.metrics import MetricsSignalAgent
from observa_investigation.agents.traces import TracesSignalAgent
from observa_investigation.orchestrator import Orchestrator
from observa_investigation.contracts import FollowUpRequest, InvestigationResult
from .watch import unanswered_legacy_request, watch


def publish(path: Path, text: str):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(text)
    Path(stream.name).replace(path)


def preserve_legacy_initial(reports, incident, folder):
    legacy = folder / "correlation.json"
    if not legacy.exists() or (folder / "correlation.initial.json").exists():
        return
    result = CorrelationResult.model_validate_json(legacy.read_bytes())
    if result.report.history or result.incident_id != incident:
        return
    # Earlier workers published only an unlabelled report. Archive it as an
    # opening report only when its cited snapshot confirms the incident was open.
    snapshots = {row.snapshot_id for row in result.correlated_findings}
    if len(snapshots) != 1:
        return
    sid = snapshots.pop()
    root, _ = load_manifest(reports, incident, sid)
    if json.loads((root / "snapshots" / sid / "incident.json").read_bytes())["status"] == "open":
        for name in ("report.md", "report.json", "investigation.json", "correlation.json"):
            source = folder / name
            if source.exists():
                publish(folder / f"{source.stem}.initial{source.suffix}", source.read_text(encoding="utf-8"))


def make_orchestrator(reports, evidence_dir=None):
    agents = {name: cls(str(reports), use_llm=True) for name, cls in (
        ("logs", LogsSignalAgent), ("metrics", MetricsSignalAgent), ("traces", TracesSignalAgent))}
    if evidence_dir and os.getenv("LOKI_URL") and os.getenv("PROMETHEUS_URL"):
        source = LiveEvidence(evidence_dir, os.environ["LOKI_URL"], os.environ["PROMETHEUS_URL"])
        agents["logs"].live = agents["metrics"].live = source
    return Orchestrator(agents)


def investigate(reports, doc, cache):
    sid = doc["snapshot_id"]
    path = cache / f"{sid}.json"
    if path.is_file():
        result = InvestigationResult.model_validate_json(path.read_bytes())
        if result.incident_id != doc["id"]:
            raise ValueError("F2 cache belongs to another incident")
        print(f"F2: using saved analysis for {doc['id']} at {sid}")
        return result
    print(f"F2: reading saved evidence for {doc['id']} at {sid}")
    result = make_orchestrator(reports).investigate(doc)
    if result.status == "failed":
        raise RuntimeError(f"F2 failed on {doc['snapshot_id']}")
    publish(path, result.model_dump_json(indent=2))
    return result


def correlate_with_follow_up(reports, cache, history, inputs, investigations, saved_request=None):
    """Checkpoint completed rounds so provider retries resume at the failed step."""
    orchestrator = make_orchestrator(reports, cache.parent / "follow-up-evidence")
    asked = set()
    for cycle in range(orchestrator.config.max_reinvestigations + 1):
        prefix = f"{history.snapshot_id}.{history.phase}.{cycle}"
        decision = cache / f"{prefix}.decision.json"
        if decision.exists():
            result = CorrelationResult.model_validate_json(decision.read_bytes())
        elif cycle == 0 and saved_request is not None:
            result = saved_request
            publish(decision, result.model_dump_json())
        else:
            print(f"F3: correlating {investigations[0].incident_id} ({history.phase}, {history.snapshot_id}) with Groq")
            result = correlate(investigations[0].model_dump(), history=history,
                               additional_investigations=[data.model_dump() for data in investigations[1:]])
            publish(decision, result.model_dump_json())
        follow = result.follow_up_request
        if follow is None:
            return result
        if cycle == orchestrator.config.max_reinvestigations:
            result.report.follow_up_stop_reason = "round_limit"
            return result
        signature = tuple(sorted((s, q.strip().casefold()) for s in follow.target_agents
                          for q in follow.signal_objectives.get(s, follow.objectives)))
        if signature in asked:
            result.report.follow_up_stop_reason = "repeated_request"
            return result
        asked.add(signature)
        for i, (doc, prior) in enumerate(zip(inputs, investigations)):
            saved = cache / f"{prefix}.{doc['snapshot_id']}.answer.json"
            if saved.exists():
                updated = InvestigationResult.model_validate_json(saved.read_bytes())
            else:
                request = FollowUpRequest.model_validate(follow.model_dump())
                request.source_run_id = prior.run_id
                print(f"F2: follow-up {cycle + 1} for {doc['id']} at {doc['snapshot_id']} ({', '.join(request.target_agents)})")
                updated = orchestrator.follow_up(request, doc, prior_result=prior, reinvestigation_count=cycle)
                publish(saved, updated.model_dump_json())
            investigations[i] = updated


def process_incident(reports: Path, incident: str, folder: Path, snapshot: str):
    folder.mkdir(parents=True, exist_ok=True)
    history, inputs = load_history(reports, incident, snapshot)
    print(f"[{incident}] Processing {history.phase} snapshot {snapshot}; "
          f"F1 status is {'resolved' if history.resolved_at else 'open'}.")
    initial = history.phase == "initial"
    suffix = ".initial" if initial else ""
    if not initial:
        preserve_legacy_initial(reports, incident, folder)
    correlation_path = folder / f"correlation{suffix}.json"
    result = None
    saved_request = None
    if correlation_path.exists():
        raw = json.loads(correlation_path.read_bytes())
        cached = CorrelationResult.model_validate(raw)
        if (unanswered_legacy_request(raw) and cached.incident_id == incident and cached.report.history
                and cached.report.history.phase == history.phase and cached.report.history.snapshot_id == snapshot):
            saved_request = cached
        elif cached.incident_id == incident and cached.report.history == history:
            result = cached
    if result is None:
        cache = folder / ".f2-cache"
        cache.mkdir(exist_ok=True)
        opening_path = folder / f"investigation{suffix}.json"
        first_cache = cache / f"{history.first_snapshot}.json"
        if saved_request and opening_path.is_file() and not first_cache.exists():
            prior = InvestigationResult.model_validate_json(opening_path.read_bytes())
            if (prior.incident_id == incident and prior.run_id == saved_request.follow_up_request.source_run_id
                    and saved_request.report.history.first_snapshot == history.first_snapshot):
                publish(first_cache, prior.model_dump_json())
        investigations = [investigate(reports, doc, cache) for doc in inputs]
        result = correlate_with_follow_up(reports, cache, history, inputs, investigations, saved_request)
        for label, data in zip(("", ".impact"), investigations):
            publish(folder / f"investigation{suffix}{label}.json", data.model_dump_json(indent=2))
        publish(correlation_path, result.model_dump_json(indent=2))
    destinations = [suffix]
    if initial:
        current = folder / "correlation.json"
        prior = CorrelationResult.model_validate_json(current.read_bytes()) if current.exists() else None
        if not prior or not prior.report.history or prior.report.history.phase != "final":
            # Existing consumers read the latest available report under the original names.
            publish(folder / "investigation.json", (folder / "investigation.initial.json").read_text(encoding="utf-8"))
            publish(current, result.model_dump_json(indent=2))
            destinations.append("")
    for target in destinations:
        publish(folder / f"report{target}.json", result.report.model_dump_json(indent=2))
        publish(folder / f"report{target}.md", render_markdown(result.report))
    print(f"[{incident}] F3 status={result.agent_status}; report status={result.report.status}; "
          f"phase={history.phase}; saved to {folder / f'report{suffix}.md'}.")
    if history.phase == "initial" and history.resolved_at is None:
        print(f"[{incident}] Still open in F1. This is the opening report; final report is queued "
              "when F1 confirms recovery.")
    elif history.phase == "final":
        print(f"[{incident}] Final report published for the resolved incident.")


def run(reports: Path, output: Path, requested: str | None):
    if requested is None:
        watch(reports, output, process_incident)
        return
    incident = requested
    if not incident.startswith("INC-") or Path(incident).name != incident or not (reports / incident / "incident.json").is_file():
        raise ValueError(f"Unknown F1 incident ID: {incident}")
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    snapshot = json.loads((reports / incident / "incident.json").read_bytes())["snapshot_id"]
    process_incident(reports, incident, Path(mkdtemp(dir=output, prefix=f"{incident}-{stamp}-")), snapshot)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("incident", nargs="?", help="Optional: run once for this ID instead of watching F1.")
    try:
        run(Path("/reports"), Path("/runs"), parser.parse_args().incident)
    except (ValueError, OSError, RuntimeError, EOFError) as exc:
        parser.exit(1, f"Investigation stopped: {exc}\n")
