"""Read saved trace evidence of one incident snapshot.

Reads trace summaries from the "traces" ref, then retrieves detailed spans
for each trace via its individual ref_id. The reader never fails silently:
anything that stops it early is returned as a "gap".
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SummaryFetcher = Callable[[int, int], dict]  # (cursor, limit) -> page dict
SpansFetcher = Callable[[str], dict]         # (trace_ref_id) -> page dict


def read_all_traces(
    fetch_summary: SummaryFetcher,
    fetch_spans: Optional[SpansFetcher] = None,
    page_size: int = 50,
    max_pages: int = 20,
    max_detail_traces: int = 20,
) -> Tuple[List[Dict[str, Any]], List[str], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Return (traces, gaps, query_window, metadata)."""
    traces: List[Dict[str, Any]] = []
    gaps: List[str] = []
    cursor = 0
    total = None
    query_window = None
    metadata = None

    for _ in range(max_pages):
        page = fetch_summary(cursor, page_size)
        if page.get("error"):
            gaps.append(f"traces read stopped at cursor {cursor}: {page['error']}")
            break
        if query_window is None and page.get("query_window"):
            query_window = page.get("query_window")
        if metadata is None and page.get("metadata"):
            metadata = page.get("metadata")

        traces.extend(page.get("items") or [])
        total = page.get("total_records", total)
        if not page.get("has_more"):
            break
        nxt = page.get("next_cursor")
        if nxt is None or nxt <= cursor:
            gaps.append("traces read stopped: cursor did not advance")
            break
        cursor = nxt
    else:
        gaps.append(f"traces read stopped after {max_pages} pages; summary sample may be incomplete")

    if total is not None and len(traces) < total:
        gaps.append(f"read {len(traces)} of {total} saved trace records")

    # Fetch detailed spans for up to max_detail_traces
    if fetch_spans:
        for t in traces[:max_detail_traces]:
            ref_id = t.get("ref_id")
            if not ref_id:
                t["spans"] = []
                continue
            try:
                spans_page = fetch_spans(ref_id)
                if spans_page.get("error"):
                    gaps.append(f"spans read error for {ref_id}: {spans_page['error']}")
                    t["spans"] = []
                else:
                    t["spans"] = spans_page.get("items") or []
            except Exception as e:
                gaps.append(f"spans fetch failed for {ref_id}: {e}")
                t["spans"] = []

    return traces, gaps, query_window, metadata


def make_fetchers(
    reports_dir, incident_id: str, snapshot_id: str, max_bytes: int = 65536
) -> Tuple[SummaryFetcher, SpansFetcher]:
    """Build fetchers on top of F1's read_evidence.

    F1's folder (alert-detection-grouping) must be on PYTHONPATH.
    """
    from observa_alerts.evidence import read_evidence  # imported lazily

    reports = Path(reports_dir).resolve()

    def fetch_summary(cursor: int, limit: int) -> dict:
        result = read_evidence(
            reports, incident_id, snapshot_id, "traces",
            cursor=cursor, limit=limit, max_bytes=max_bytes,
        )
        return json.loads(result) if isinstance(result, str) else result

    def fetch_spans(trace_ref_id: str) -> dict:
        result = read_evidence(
            reports, incident_id, snapshot_id, trace_ref_id,
            cursor=0, limit=100, max_bytes=max_bytes,
        )
        return json.loads(result) if isinstance(result, str) else result

    return fetch_summary, fetch_spans
