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
from correlation_agent.agent.contract import (failed_contract_markdown, failed_contract_result,
                                               to_contract_result)
from correlation_agent.agent.history import load_history
from observa_investigation.agents.live_evidence import LiveEvidence
from observa_investigation.agents.logs import LogsSignalAgent
from observa_investigation.agents.metrics import MetricsSignalAgent
from observa_investigation.agents.traces import TracesSignalAgent
from observa_investigation.orchestrator import Orchestrator
from observa_investigation.contracts import FollowUpRequest, InvestigationResult
from .memory import WorkerMemoryLookup
from .watch import unanswered_legacy_request, watch


def publish(path: Path, text: str):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(text)
    Path(stream.name).replace(path)


def preserve_legacy_initial(reports, incident, folder):
    legacy = folder / "correlation.json"
    state_dir = folder / "f3" / ".state"
    initial_state = state_dir / "correlation.initial.json"
    if not legacy.exists() or initial_state.exists():
        return
    result = CorrelationResult.model_validate_json(legacy.read_bytes())
    if result.report.history or result.incident_id != incident:
        return
    snapshots = {row.snapshot_id for row in result.correlated_findings}
    if len(snapshots) != 1:
        return
    sid = snapshots.pop()
    root, _ = load_manifest(reports, incident, sid)
    if json.loads((root / "snapshots" / sid / "incident.json").read_bytes())["status"] != "open":
        return

    f2_dir = folder / "f2"
    f3_dir = folder / "f3"
    (f2_dir / ".cache").mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    legacy_investigation = folder / "investigation.json"
    if legacy_investigation.exists():
        publish(f2_dir / "investigation.initial.json",
                legacy_investigation.read_text(encoding="utf-8"))
        investigation = InvestigationResult.model_validate_json(legacy_investigation.read_bytes())
        publish(f2_dir / ".cache" / f"{sid}.json", investigation.model_dump_json())
    publish(initial_state, result.model_dump_json(indent=2))
    public = to_contract_result(result)
    markdown = render_markdown(result.report)
    publish(f3_dir / "output.initial.json", public.model_dump_json(indent=2))
    publish(f3_dir / "output.initial.md", markdown)
    if not (state_dir / "correlation.json").exists():
        publish(f3_dir / "output.json", public.model_dump_json(indent=2))
        publish(f3_dir / "output.md", markdown)


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


def correlate_with_follow_up(reports, cache, history, inputs, investigations, saved_request=None, state_dir=None):
    """Checkpoint completed rounds so provider retries resume at the failed step."""
    state_dir = state_dir or cache
    state_dir.mkdir(parents=True, exist_ok=True)
    orchestrator = make_orchestrator(reports, cache.parent / "follow-up-evidence")
    asked = set()
    for cycle in range(orchestrator.config.max_reinvestigations + 1):
        prefix = f"{history.snapshot_id}.{history.phase}.{cycle}"
        decision = state_dir / f"{prefix}.decision.json"
        if decision.exists():
            result = CorrelationResult.model_validate_json(decision.read_bytes())
        elif cycle == 0 and saved_request is not None:
            result = saved_request
            publish(decision, result.model_dump_json())
        else:
            print(f"F3: correlating {investigations[0].incident_id} ({history.phase}, {history.snapshot_id}) with Groq")
            lookup = WorkerMemoryLookup(inputs[-1], investigations)
            result = correlate(investigations[0].model_dump(), memory_lookup=lookup, history=history,
                               additional_investigations=[data.model_dump() for data in investigations[1:]])
            result.report.memory_search = lookup.summary
            publish(decision, result.model_dump_json())
        follow = result.follow_up_request
        if follow is None:
            return result
        if cycle == orchestrator.config.max_reinvestigations:
            follow.status = "rejected"
            result.report.follow_up_stop_reason = "round_limit"
            return result
        signature = tuple(sorted((s, q.strip().casefold()) for s in follow.target_agents
                          for q in follow.signal_objectives.get(s, follow.objectives)))
        if signature in asked:
            follow.status = "rejected"
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

    phase_suffix = ".initial" if history.phase == "initial" else ""
    if history.phase == "final":
        preserve_legacy_initial(reports, incident, folder)
    f2_dir = folder / "f2"
    f3_dir = folder / "f3"
    cache = f2_dir / ".cache"
    state_dir = f3_dir / ".state"
    f2_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    legacy_state = folder / f"correlation{phase_suffix}.json"
    correlation_path = state_dir / f"correlation{phase_suffix}.json"
    if not correlation_path.exists() and legacy_state.exists():
        publish(correlation_path, legacy_state.read_text(encoding="utf-8"))

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
        old_cache = folder / ".f2-cache"
        if old_cache.exists():
            for item in old_cache.iterdir():
                target_dir = state_dir if item.name.endswith(".decision.json") else cache
                target = target_dir / item.name
                if item.is_file() and not target.exists():
                    publish(target, item.read_text(encoding="utf-8"))
        opening_path = f2_dir / f"investigation{phase_suffix}.json"
        if not opening_path.exists():
            legacy_opening = folder / f"investigation{phase_suffix}.json"
            if legacy_opening.exists():
                publish(opening_path, legacy_opening.read_text(encoding="utf-8"))
        first_cache = cache / f"{history.first_snapshot}.json"
        if saved_request and opening_path.is_file() and not first_cache.exists():
            prior = InvestigationResult.model_validate_json(opening_path.read_bytes())
            if (prior.incident_id == incident and prior.run_id == saved_request.follow_up_request.source_run_id
                    and saved_request.report.history.first_snapshot == history.first_snapshot):
                publish(first_cache, prior.model_dump_json())
        investigations = [investigate(reports, doc, cache) for doc in inputs]
        result = correlate_with_follow_up(reports, cache, history, inputs, investigations,
                                         saved_request, state_dir)
        for label, data in zip(("", ".impact"), investigations):
            publish(f2_dir / f"investigation{phase_suffix}{label}.json", data.model_dump_json(indent=2))
        publish(correlation_path, result.model_dump_json(indent=2))

    public_result = to_contract_result(result)
    markdown = render_markdown(result.report)
    output_suffix = ".initial" if history.phase == "initial" else ""
    json_path = f3_dir / f"output{output_suffix}.json"
    markdown_path = f3_dir / f"output{output_suffix}.md"
    publish(json_path, public_result.model_dump_json(indent=2))
    publish(markdown_path, markdown)

    if history.phase == "initial":
        latest_json = f3_dir / "output.json"
        latest_markdown = f3_dir / "output.md"
        final_state = state_dir / "correlation.json"
        has_final = False
        if final_state.exists():
            prior = CorrelationResult.model_validate_json(final_state.read_bytes())
            has_final = bool(prior.report.history and prior.report.history.phase == "final")
        if not has_final:
            publish(latest_json, public_result.model_dump_json(indent=2))
            publish(latest_markdown, markdown)

    print(f"[{incident}] F3 status={public_result.status}; phase={history.phase}; "
          f"JSON={json_path}; Markdown={markdown_path}.")
    if history.phase == "initial" and history.resolved_at is None:
        print(f"[{incident}] Still open in F1. This is the opening output; final output is queued "
              "when F1 confirms recovery.")
    elif history.phase == "final":
        print(f"[{incident}] Final output published for the resolved incident.")


def publish_terminal_failure(reports, incident, folder, snapshot, phase, message, retryable, error_type):
    history, _ = load_history(reports, incident, snapshot)
    f3_dir = folder / "f3"
    f3_dir.mkdir(parents=True, exist_ok=True)
    result = failed_contract_result(incident, phase, snapshot, message, retryable=retryable,
                                   error_type=error_type)
    suffix = ".initial" if phase == "initial" else ""
    json_path = f3_dir / f"output{suffix}.json"
    markdown_path = f3_dir / f"output{suffix}.md"
    markdown = failed_contract_markdown(result)
    publish(json_path, result.model_dump_json(indent=2))
    publish(markdown_path, markdown)
    if phase == "initial" and not (f3_dir / "output.json").exists():
        publish(f3_dir / "output.json", result.model_dump_json(indent=2))
        publish(f3_dir / "output.md", markdown)
    print(f"[{incident}] F3 status=failed; phase={phase}; JSON={json_path}; Markdown={markdown_path}.")


process_incident.on_failure = publish_terminal_failure


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
    folder = Path(mkdtemp(dir=output, prefix=f"{incident}-{stamp}-"))
    try:
        process_incident(reports, incident, folder, snapshot)
    except Exception as exc:
        publish_terminal_failure(reports, incident, folder, snapshot,
            "final" if json.loads((reports / incident / "incident.json").read_bytes())["status"] == "resolved" else "initial",
            str(exc), getattr(exc, "retryable", True), type(exc).__name__)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("incident", nargs="?", help="Optional: run once for this ID instead of watching F1.")
    try:
        run(Path("/reports"), Path("/runs"), parser.parse_args().incident)
    except (ValueError, OSError, RuntimeError, EOFError) as exc:
        parser.exit(1, f"Investigation stopped: {exc}\n")
