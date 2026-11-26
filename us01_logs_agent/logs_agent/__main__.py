"""Run the Logs Agent on one saved incident.

PowerShell:
  $env:PYTHONPATH = "C:\\path\\to\\Observa-F1\\alert-detection-grouping"
  python -m logs_agent --reports C:\\path\\to\\...\\reports --incident INC-... --snapshot r19-...

Default: print the deterministic profile (no LLM).
--agent:           also run the LLM step and print the finding
--agent --no-llm:  print the deterministic finding without calling any model

The LLM is configured from environment variables, see logs_agent/llm.py.
Without --snapshot the latest snapshot named in incident.json is used.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .finding import build_finding
from .llm import LLMClient, LLMError
from .profiler import profile_logs
from .reader import make_fetcher, read_all_logs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--reports", required=True, help="F1 reports folder")
    ap.add_argument("--incident", required=True, help="incident folder name, e.g. INC-...")
    ap.add_argument("--snapshot", help="pin a snapshot id (default: the latest one)")
    ap.add_argument("--agent", action="store_true", help="print the finding instead of the profile")
    ap.add_argument("--no-llm", action="store_true", help="with --agent: skip the model call")
    args = ap.parse_args()

    incident = json.loads(
        (Path(args.reports) / args.incident / "incident.json").read_text(encoding="utf-8")
    )
    snapshot_id = args.snapshot or incident["snapshot_id"]
    fetch = make_fetcher(args.reports, incident["id"], snapshot_id)
    records, gaps = read_all_logs(fetch)
    profile = profile_logs(records, incident, read_gaps=gaps)
    profile["incident_id"] = incident["id"]
    profile["snapshot_id"] = snapshot_id

    if not args.agent:
        print(json.dumps(profile, indent=2, ensure_ascii=False))
        return

    llm = None
    if not args.no_llm and profile["sample"]["records"] > 0:
        try:
            llm = LLMClient.from_env()
        except LLMError as e:
            print(f"error: {e}", file=sys.stderr)
            raise SystemExit(2)
    print(json.dumps(build_finding(profile, incident, llm), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
