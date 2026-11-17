r"""Run one investigation on a saved F1 incident.

PowerShell (F1 folder + Logs Agent folder + Metrics Agent folder + Traces Agent folder + this folder on PYTHONPATH):
  $env:PYTHONPATH = "C:\path\to\alert-detection-grouping;C:\path\to\us01_logs_agent;C:\path\to\us02_metrics_agent;C:\path\to\us03_traces_agent;C:\path\to\investigation"
  python -m observa_investigation --reports "$base\demo-pool" --incident INC-... [--no-llm]

All three specialist agents (Logs, Metrics, Traces) are fully connected!
"""
import argparse
import json
import sys
from pathlib import Path

from .agents.logs import LogsSignalAgent
from .agents.metrics import MetricsSignalAgent
from .agents.traces import TracesSignalAgent
from .config import InvestigationConfig
from .orchestrator import Orchestrator


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--reports", required=True)
    ap.add_argument("--incident", required=True, help="incident folder name, e.g. INC-...")
    ap.add_argument("--snapshot", help="pin a snapshot id (default: the latest in incident.json)")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--timeout", type=float, default=180.0, help="seconds per dispatch round")
    ap.add_argument("--retries", type=int, default=1)
    a = ap.parse_args()

    incident = json.loads((Path(a.reports) / a.incident / "incident.json").read_text(encoding="utf-8"))
    if a.snapshot:
        incident["snapshot_id"] = a.snapshot
    if not incident.get("snapshot_id"):
        sys.exit("error: incident.json has no snapshot_id; pass --snapshot")

    agents = {
        "logs": LogsSignalAgent(a.reports, use_llm=not a.no_llm),
        "metrics": MetricsSignalAgent(a.reports, use_llm=not a.no_llm),
        "traces": TracesSignalAgent(a.reports, use_llm=not a.no_llm),
    }
    cfg = InvestigationConfig(agent_timeout_s=a.timeout, max_retries=a.retries)
    print(Orchestrator(agents, cfg).investigate(incident).model_dump_json(indent=2))


if __name__ == "__main__":
    main()
