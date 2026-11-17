"""Read every saved log record of one incident snapshot, page by page.

The reader never fails silently: anything that stops it early is returned
as a "gap" so the agent can state it in its findings.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

Fetcher = Callable[[int, int], dict]  # (cursor, limit) -> page dict


def read_all_logs(fetch_page: Fetcher, page_size: int = 50, max_pages: int = 20):
    """Return (records, gaps)."""
    records: list[dict] = []
    gaps: list[str] = []
    cursor = 0
    total = None
    for _ in range(max_pages):
        page = fetch_page(cursor, page_size)
        if page.get("error"):
            gaps.append(f"logs read stopped at cursor {cursor}: {page['error']}")
            break
        records.extend(page.get("items") or [])
        total = page.get("total_records", total)
        if not page.get("has_more"):
            break
        nxt = page.get("next_cursor")
        if nxt is None or nxt <= cursor:
            gaps.append("logs read stopped: cursor did not advance")
            break
        cursor = nxt
    else:
        gaps.append(f"logs read stopped after {max_pages} pages; sample may be incomplete")

    if total is not None and len(records) < total:
        gaps.append(f"read {len(records)} of {total} saved log records")
    return records, gaps


def make_fetcher(reports_dir, incident_id: str, snapshot_id: str, max_bytes: int = 65536) -> Fetcher:
    """Build a fetcher on top of F1's read_evidence.

    F1's folder (alert-detection-grouping) must be on PYTHONPATH.
    """
    from observa_alerts.evidence import read_evidence  # imported lazily: tests do not need F1

    reports = Path(reports_dir).resolve()

    def fetch(cursor: int, limit: int) -> dict:
        result = read_evidence(
            reports, incident_id, snapshot_id, "logs",
            cursor=cursor, limit=limit, max_bytes=max_bytes,
        )
        return json.loads(result) if isinstance(result, str) else result

    return fetch
