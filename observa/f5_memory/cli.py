"""Local demo entry point. No HTTP server and no write endpoint.

``import-handoffs`` replays F6 handoff files (one JSON per approved decision, as F6's
``FileMemorySink`` stand-in writes them) and reads each approved version from the review
service with GET requests. The viewer token comes from ``OBSERVA_REVIEW_TOKEN`` and is never printed.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from .f6_handoff import DEFAULT_REVIEW_URL, HttpReviewServiceClient, IncidentMemorySink
from .service import DEFAULT_DATABASE, IncidentMemory
from .sources import FakeApprovalSource
from .validation import MemoryOperationError

TOKEN_ENV = "OBSERVA_REVIEW_TOKEN"


def _seed(args):
    source = FakeApprovalSource.from_file(args.fixtures)
    memory = IncidentMemory(args.db, source)
    outcomes = []
    for decision_id in source.decision_ids():
        try:
            saved = memory.save_approved_incident(decision_id)
            outcomes.append({"decision_id": decision_id, "status": saved["status"],
                             "created": saved["created"], "knowledge_id": saved["item"]["knowledge_id"]})
        except MemoryOperationError as exc:
            if exc.code != "NOT_APPROVED":
                raise
            outcomes.append({"decision_id": decision_id, "status": "excluded", "reason": str(exc)})
    return {"data_source": "SIMULATED DEVELOPMENT DATA", "results": outcomes}, 0


def _import_handoffs(args):
    client = HttpReviewServiceClient(args.review_url, os.environ.get(TOKEN_ENV) or None, args.timeout)
    sink = IncidentMemorySink.connect(args.db, client)
    outcomes, failed = [], False
    for path in sorted(Path(args.directory).glob("*.json")):
        try:
            saved = sink.save(json.loads(path.read_text(encoding="utf-8")))
            outcomes.append({"file": path.name, "status": saved["status"], "created": saved["created"],
                             "knowledge_id": saved["item"]["knowledge_id"]})
        except MemoryOperationError as exc:
            failed = True
            outcomes.append({"file": path.name, "status": "failed", "error": exc.envelope(str(uuid4()))})
        except (OSError, ValueError):
            failed = True
            outcomes.append({"file": path.name, "status": "failed",
                             "error": {"code": "VALIDATION_ERROR", "message": "Cannot read a JSON handoff from this file"}})
    return {"data_source": f"F6/F7 review service at {client.base_url}", "results": outcomes}, 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="OBSERVA Feature 5 local incident memory")
    parser.add_argument("--db", default=DEFAULT_DATABASE, help=f"SQLite file (default: {DEFAULT_DATABASE})")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed-demo", help="Load explicitly simulated approvals from local fixtures")
    seed.add_argument("--fixtures", default="fixtures/approval_snapshots.json")
    replay = commands.add_parser("import-handoffs", help="Store approvals from F6 handoff files, read-checked against F6/F7")
    replay.add_argument("directory", help="Folder of handoff JSON files (F6's MEMORY_SINK_DIR)")
    replay.add_argument("--review-url", default=DEFAULT_REVIEW_URL)
    replay.add_argument("--timeout", type=float, default=10.0)
    search = commands.add_parser("search", help="Search latest approved historical records")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--service", action="append", default=[])
    search.add_argument("--limit", type=int, default=5)
    get = commands.add_parser("get", help="Inspect a stored knowledge ID")
    get.add_argument("knowledge_id")
    history = commands.add_parser("history", help="Inspect approved revisions for an incident")
    history.add_argument("incident_id")
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        if args.command == "seed-demo":
            result, exit_code = _seed(args)
        elif args.command == "import-handoffs":
            result, exit_code = _import_handoffs(args)
        else:
            memory = IncidentMemory(args.db)
            if args.command == "search":
                result = memory.search_approved_incidents(args.query, args.service, args.limit)
                exit_code = 1 if result["status"] == "failed" else 0
            elif args.command == "history":
                result = {"items": memory.list_approved_versions(args.incident_id)}
            else:
                result = memory.get_approved_knowledge(args.knowledge_id)
                if result is None:
                    raise MemoryOperationError("NOT_FOUND", "Knowledge ID not found")
    except MemoryOperationError as exc:
        result = {"status": "failed", "error": exc.envelope(str(uuid4()))}
        exit_code = 1
    except (OSError, ValueError, KeyError, TypeError):
        result = {"status": "failed", "error": {"code": "FIXTURE_ERROR", "message": "Cannot read valid demo fixtures"}}
        exit_code = 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
