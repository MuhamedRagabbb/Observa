"""Deterministic profiler for the saved trace sample of one OBSERVA incident.

No LLM in this file. Anything the agent later says about traces must be
traceable to this output.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple


def _p95(values: List[float]) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    idx = max(0, -(-len(s) * 95 // 100) - 1)
    return round(s[idx], 2)


def profile_traces(
    traces: List[Dict[str, Any]],
    incident: Dict[str, Any],
    read_gaps: Optional[List[str]] = None,
    query_window: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    gaps = list(read_gaps or [])

    all_spans: List[Dict[str, Any]] = []
    failed_spans: List[Dict[str, Any]] = []

    # Map trace_id -> list of spans
    trace_span_map: Dict[str, List[Dict[str, Any]]] = {}
    for t in traces:
        tid = t.get("trace_id") or "unknown"
        spans = t.get("spans") or []
        trace_span_map[tid] = spans
        for s in spans:
            s_copy = dict(s)
            s_copy["trace_id"] = tid
            all_spans.append(s_copy)
            is_err = bool(s.get("error"))
            status = s.get("http_status_code")
            if is_err or (isinstance(status, int) and status >= 400):
                failed_spans.append(s_copy)

    # Group failed spans by (service, operation, status)
    failed_groups: Dict[Tuple[str, str, Optional[int]], List[Dict[str, Any]]] = defaultdict(list)
    for s in failed_spans:
        svc = s.get("service") or "unknown"
        op = s.get("operation") or "unknown"
        st = s.get("http_status_code")
        failed_groups[(svc, op, st)].append(s)

    # Group all spans by (service, operation) for duration analysis
    op_groups: Dict[Tuple[str, str], List[float]] = defaultdict(list)
    for s in all_spans:
        svc = s.get("service") or "unknown"
        op = s.get("operation") or "unknown"
        dur_us = s.get("duration_us")
        if isinstance(dur_us, (int, float)):
            op_groups[(svc, op)].append(dur_us / 1000.0)

    # Call hierarchy & deepest failing span analysis
    propagation_paths: Set[str] = set()
    deepest_failing_counts: Dict[str, int] = defaultdict(int)

    for tid, spans in trace_span_map.items():
        if not spans:
            continue
        id_to_span = {s["span_id"]: s for s in spans if s.get("span_id")}
        child_to_parent: Dict[str, str] = {}
        parent_to_children: Dict[str, List[str]] = defaultdict(list)

        for s in spans:
            sid = s.get("span_id")
            for ref in s.get("references") or []:
                if ref.get("refType") == "CHILD_OF":
                    pid = ref.get("spanID")
                    if pid and sid:
                        child_to_parent[sid] = pid
                        parent_to_children[pid].append(sid)

        # Build service paths
        roots = [s for s in spans if s.get("span_id") not in child_to_parent]
        for r in roots:
            curr_path = [r.get("service") or "unknown"]
            # DFS path
            stack = [(r["span_id"], curr_path)]
            while stack:
                curr_sid, path = stack.pop()
                children = parent_to_children.get(curr_sid, [])
                if not children:
                    # deduplicate adjacent services
                    dedup_path = []
                    for svc in path:
                        if not dedup_path or dedup_path[-1] != svc:
                            dedup_path.append(svc)
                    if len(dedup_path) > 1:
                        propagation_paths.add(" -> ".join(dedup_path))
                for cid in children:
                    c_span = id_to_span.get(cid)
                    c_svc = c_span.get("service") if c_span else "unknown"
                    stack.append((cid, path + [c_svc]))

        # Find deepest failing span in this trace
        trace_failed = [s for s in spans if s.get("error") or (isinstance(s.get("http_status_code"), int) and s.get("http_status_code") >= 400)]
        if trace_failed:
            # depth = count of ancestors
            def depth(sid):
                d = 0
                while sid in child_to_parent:
                    sid = child_to_parent[sid]
                    d += 1
                return d
            deepest = max(trace_failed, key=lambda s: depth(s.get("span_id")))
            deepest_failing_counts[deepest.get("service") or "unknown"] += 1

    deepest_failure_origin = None
    if deepest_failing_counts:
        deepest_failure_origin = max(deepest_failing_counts.items(), key=lambda x: x[1])[0]

    # Build pattern items (T1, T2...)
    patterns: List[Dict[str, Any]] = []
    idx = 1

    # First: failed span patterns sorted by count
    for (svc, op, st), span_list in sorted(failed_groups.items(), key=lambda x: -len(x[1])):
        durs = [s["duration_us"] / 1000.0 for s in span_list if isinstance(s.get("duration_us"), (int, float))]
        ex = span_list[0]
        patterns.append({
            "id": f"T{idx}",
            "type": "failed_span",
            "service": svc,
            "operation": op,
            "status": st,
            "count": len(span_list),
            "p95_duration_ms": _p95(durs),
            "max_duration_ms": round(max(durs), 2) if durs else None,
            "trace_id": ex.get("trace_id"),
            "span_id": ex.get("span_id"),
            "is_deepest_origin": (svc == deepest_failure_origin),
        })
        idx += 1

    # Second: slow span patterns (if max_duration > 1000ms and not already in patterns)
    for (svc, op), durs in sorted(op_groups.items(), key=lambda x: -max(x[1]) if x[1] else 0):
        if durs and (max(durs) >= 1000.0 or _p95(durs) >= 500.0):
            # check if already added as failed span
            already = any(p["service"] == svc and p["operation"] == op for p in patterns)
            if not already:
                patterns.append({
                    "id": f"T{idx}",
                    "type": "slow_span",
                    "service": svc,
                    "operation": op,
                    "status": None,
                    "count": len(durs),
                    "p95_duration_ms": _p95(durs),
                    "max_duration_ms": round(max(durs), 2),
                    "trace_id": None,
                    "span_id": None,
                    "is_deepest_origin": False,
                })
                idx += 1

    seen = sorted({s.get("service") for s in all_spans if s.get("service") and s.get("service") != "unknown"})
    # also include services seen in trace summary if spans were not fetched
    if not seen:
        seen = sorted({svc for t in traces for svc in t.get("services") or []})

    affected = incident.get("affected_services", [])
    missing = [s for s in affected if s not in seen]

    limits: List[str] = []
    if not traces:
        limits.append("trace evidence could not be read" if gaps else "no trace evidence in this snapshot")
    else:
        limits.append(
            f"sample of {len(traces)} retained traces ({len(all_spans)} spans); "
            "traces represent bounded snapshots, not continuous distributed trace history"
        )
    for s in missing:
        limits.append(f"no traces or spans found for affected service: {s}")
    limits.extend(gaps)

    return {
        "sample": {
            "traces_count": len(traces),
            "spans_count": len(all_spans),
            "failed_spans_count": len(failed_spans),
            "query_window": query_window,
            "coverage": (metadata or {}).get("coverage"),
        },
        "patterns": patterns,
        "deepest_failure_origin": deepest_failure_origin,
        "propagation_paths": sorted(list(propagation_paths)),
        "services_seen": seen,
        "services_missing": missing,
        "limits": limits,
        "read_gaps": gaps,
    }
