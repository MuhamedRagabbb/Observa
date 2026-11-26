"""Read saved metrics evidence of one incident snapshot, page by page.

The reader never fails silently: anything that stops it early is returned
as a "gap" so the agent can report it in its findings.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Tuple, List, Dict, Any, Optional

Fetcher = Callable[[int, int], dict]  # (cursor, limit) -> page dict


def read_all_metrics(fetch_page: Fetcher, page_size: int = 50, max_pages: int = 20) -> Tuple[List[Dict[str, Any]], List[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return (observations, gaps, query_window, metadata)."""
    observations: List[Dict[str, Any]] = []
    gaps: List[str] = []
    cursor = 0
    total = None
    query_window = None
    metadata = None

    for _ in range(max_pages):
        page = fetch_page(cursor, page_size)
        if page.get("error"):
            gaps.append(f"metrics read stopped at cursor {cursor}: {page['error']}")
            break
        if query_window is None and page.get("query_window"):
            query_window = page.get("query_window")
        if metadata is None and page.get("metadata"):
            metadata = page.get("metadata")

        observations.extend(page.get("items") or [])
        total = page.get("total_records", total)
        if not page.get("has_more"):
            break
        nxt = page.get("next_cursor")
        if nxt is None or nxt <= cursor:
            gaps.append("metrics read stopped: cursor did not advance")
            break
        cursor = nxt
    else:
        gaps.append(f"metrics read stopped after {max_pages} pages; sample may be incomplete")

    if total is not None and len(observations) < total:
        gaps.append(f"read {len(observations)} of {total} saved metric records")
    return observations, gaps, query_window, metadata


def make_fetcher(reports_dir, incident_id: str, snapshot_id: str, max_bytes: int = 65536) -> Fetcher:
    """Build a fetcher on top of F1's read_evidence.

    F1's folder (alert-detection-grouping) must be on PYTHONPATH.
    """
    from observa_alerts.evidence import read_evidence  # imported lazily

    reports = Path(reports_dir).resolve()

    def fetch(cursor: int, limit: int) -> dict:
        result = read_evidence(
            reports, incident_id, snapshot_id, "metrics",
            cursor=cursor, limit=limit, max_bytes=max_bytes,
        )
        return json.loads(result) if isinstance(result, str) else result

    return fetch
