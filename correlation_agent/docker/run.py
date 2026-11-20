"""Watch for new F1 incidents, or process a single incident ID explicitly."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp

from observa_alerts.evidence import load_manifest
from correlation_agent import correlate
from correlation_agent.reporting.report import render_markdown
from correlation_agent.agent.state import CorrelationResult
from correlation_agent.agent.history import load_history
from observa_investigation.agents.logs import LogsSignalAgent
from observa_investigation.agents.metrics import MetricsSignalAgent
from observa_investigation.agents.traces import TracesSignalAgent
from observa_investigation.orchestrator import Orchestrator
from .watch import watch


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


def investigate(reports, doc):
    print(f"F2: reading saved evidence for {doc['id']} at {doc['snapshot_id']}")
    agents = {name: cls(str(reports), use_llm=True) for name, cls in (
        ("logs", LogsSignalAgent), ("metrics", MetricsSignalAgent), ("traces", TracesSignalAgent))}
    result = Orchestrator(agents).investigate(doc)
    if result.status == "failed":
        raise RuntimeError(f"F2 failed on {doc['snapshot_id']}")
    return result.model_dump(mode="json")


def process_incident(reports: Path, incident: str, folder: Path, snapshot: str):
    folder.mkdir(parents=True, exist_ok=True)
    history, inputs = load_history(reports, incident, snapshot)
    initial = history.phase == "initial"
    suffix = ".initial" if initial else ""
    if not initial:
        preserve_legacy_initial(reports, incident, folder)
    correlation_path = folder / f"correlation{suffix}.json"
    result = None
    if correlation_path.exists():
        cached = CorrelationResult.model_validate_json(correlation_path.read_bytes())
        if cached.incident_id == incident and cached.report.history == history:
            result = cached
    if result is None:
        investigations = [investigate(reports, doc) for doc in inputs]
        print(f"F3: correlating {incident} ({history.phase}, {snapshot}) with Groq")
        result = correlate(investigations[0], history=history, additional_investigations=investigations[1:])
        for label, data in zip(("", ".impact"), investigations):
            publish(folder / f"investigation{suffix}{label}.json", json.dumps(data, ensure_ascii=False, indent=2))
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
    print(f"{incident}: {result.agent_status}, report saved to {folder / f'report{suffix}.md'}")


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
