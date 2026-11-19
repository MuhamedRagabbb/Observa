"""Watch for new F1 incidents, or process a single incident ID explicitly."""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp

from correlation_agent import correlate
from correlation_agent.reporting.report import render_markdown
from correlation_agent.agent.state import CorrelationResult
from .watch import watch


def publish(path: Path, text: str):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(text)
    Path(stream.name).replace(path)


def process_incident(reports: Path, incident: str, folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    investigation_path = folder / "investigation.json"
    if not investigation_path.exists():
        print(f"F2: reading saved evidence for {incident}")
        command = [sys.executable, "-m", "observa_investigation", "--reports", str(reports),
                   "--incident", incident, "--no-llm"]
        f2 = subprocess.run(command, stdout=subprocess.PIPE, check=True)
        investigation = json.loads(f2.stdout)
        if investigation["status"] == "failed":
            raise RuntimeError("F2 failed to investigate this incident")
        publish(investigation_path, json.dumps(investigation, ensure_ascii=False, indent=2))
    correlation_path = folder / "correlation.json"
    if correlation_path.exists():
        result = CorrelationResult.model_validate_json(correlation_path.read_bytes())
    else:
        print(f"F3: correlating {incident} with Groq")
        result = correlate(json.loads(investigation_path.read_bytes()))
        publish(correlation_path, result.model_dump_json(indent=2))
    publish(folder / "report.json", result.report.model_dump_json(indent=2))
    publish(folder / "report.md", render_markdown(result.report))
    print(f"{incident}: {result.agent_status}, report saved to {folder / 'report.md'}")


def run(reports: Path, output: Path, requested: str | None):
    if requested is None:
        watch(reports, output, process_incident)
        return
    incident = requested
    if not incident.startswith("INC-") or Path(incident).name != incident or not (reports / incident / "incident.json").is_file():
        raise ValueError(f"Unknown F1 incident ID: {incident}")
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    process_incident(reports, incident, Path(mkdtemp(dir=output, prefix=f"{incident}-{stamp}-")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("incident", nargs="?", help="Optional: run once for this ID instead of watching F1.")
    try:
        run(Path("/reports"), Path("/runs"), parser.parse_args().incident)
    except (ValueError, OSError, RuntimeError, EOFError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Investigation stopped: {exc}\n")
