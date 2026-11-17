"""Deterministic profiler for the saved log sample of one OBSERVA incident.

No LLM in this file. Anything the agent later says about the logs must be
traceable to this output.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

# Verify these sets against the `declines` demo before trusting them.
DECLINE_ERROR_TYPES = {"payment_declined"}
NOISE_ERROR_TYPES = {"product_not_found"}
NOISE_EVENTS = {"validation_failed"}


def _parse(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _ts(rec: dict) -> datetime:
    raw = rec.get("timestamp")
    if raw:
        return _parse(raw)
    return datetime.fromtimestamp(float(rec["observed_at"]), tz=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def classify(rec: dict) -> str:
    """service_failure | business_decline | client_noise | other"""
    status = rec.get("http.status_code")
    etype = rec.get("error.type")
    is_int = isinstance(status, int)
    if status == 402 or etype in DECLINE_ERROR_TYPES:
        return "business_decline"
    if (
        etype in NOISE_ERROR_TYPES
        or rec.get("event") in NOISE_EVENTS
        or (is_int and 400 <= status < 500)
    ):
        return "client_noise"
    if (is_int and status >= 500) or rec.get("level") == "ERROR":
        return "service_failure"
    return "other"


def _p95(values: list[float]):
    if not values:
        return None
    s = sorted(values)
    return s[max(0, -(-len(s) * 95 // 100) - 1)]  # nearest rank


def profile_logs(records: list[dict], incident: dict, read_gaps: list[str] | None = None) -> dict:
    gaps = list(read_gaps or [])
    recs = sorted(records, key=_ts)
    counts: dict[str, int] = defaultdict(int)
    groups: dict[tuple, dict] = {}
    others: dict[tuple, dict] = {}

    for r in recs:
        cat = classify(r)
        counts[cat] += 1
        if cat == "other":
            o = others.setdefault((r.get("service.name"), r.get("event"), r.get("level")),
                                  {"count": 0, "first": None, "last": None})
            t = _ts(r)
            o["count"] += 1
            o["first"] = t if o["first"] is None else min(o["first"], t)
            o["last"] = t if o["last"] is None else max(o["last"], t)
            continue
        if cat not in ("service_failure", "business_decline"):
            continue
        key = (cat, r.get("service.name"), r.get("event"), r.get("error.type"),
               r.get("http.status_code"), r.get("http.route"))
        g = groups.setdefault(key, {"count": 0, "first": None, "last": None, "durations": []})
        t = _ts(r)
        g["count"] += 1
        g["first"] = t if g["first"] is None else min(g["first"], t)
        g["last"] = t if g["last"] is None else max(g["last"], t)
        d = r.get("duration_ms")
        if isinstance(d, (int, float)):
            g["durations"].append(d)

    patterns = []
    for k, g in groups.items():
        window = (g["last"] - g["first"]).total_seconds()
        patterns.append({
            "category": k[0], "service": k[1], "event": k[2], "error_type": k[3],
            "status": k[4], "route": k[5], "count": g["count"],
            "first_seen_in_sample": _iso(g["first"]), "last_seen_in_sample": _iso(g["last"]),
            "window_seconds": round(window, 1),
            "rate_per_s": round(g["count"] / window, 2) if window >= 1 else None,
            "p95_duration_ms": _p95(g["durations"]),
        })
    patterns.sort(key=lambda p: -p["count"])

    other_events = sorted(
        ({"service": k[0], "event": k[1], "level": k[2], "count": o["count"],
          "first_seen_in_sample": _iso(o["first"]), "last_seen_in_sample": _iso(o["last"])}
         for k, o in others.items()),
        key=lambda e: -e["count"],
    )

    seen = sorted({r.get("service.name") for r in recs if r.get("service.name")})
    affected = incident.get("affected_services", [])
    missing = [s for s in affected if s not in seen]

    releases: dict[str, set] = defaultdict(set)
    for r in recs:
        if r.get("service.version") or r.get("deployment.id"):
            releases[r.get("service.name")].add((r.get("service.version"), r.get("deployment.id")))
    release_changes = {
        svc: [{"service.version": v, "deployment.id": d} for v, d in sorted(vals, key=str)]
        for svc, vals in releases.items() if len(vals) > 1
    }

    by_trace: dict[str, set] = defaultdict(set)
    for r in recs:
        if r.get("trace_id"):
            by_trace[r["trace_id"]].add(r.get("service.name"))
    cross_service_traces = sum(1 for s in by_trace.values() if len(s) > 1)

    sample_from = _ts(recs[0]) if recs else None
    sample_to = _ts(recs[-1]) if recs else None
    first_observed = incident.get("first_observed_at")
    covers_start = None
    if sample_from and first_observed:
        covers_start = sample_from <= _parse(first_observed)

    limits: list[str] = []
    if not recs:
        limits.append("log evidence could not be read" if gaps else "no log evidence in this snapshot")
    else:
        limits.append(f"sample of {len(recs)} retained sanitized records, not all source logs")
    if covers_start is False:
        limits.append(
            f"sample starts at {_iso(sample_from)}, after incident start {first_observed}; "
            "first_seen values are first-in-sample, not onset"
        )
    for s in missing:
        limits.append(f"no log records from {s}")
    limits.extend(gaps)

    return {
        "sample": {
            "records": len(recs),
            "from": _iso(sample_from) if sample_from else None,
            "to": _iso(sample_to) if sample_to else None,
            "covers_incident_start": covers_start,
        },
        "categories": dict(counts),
        "patterns": patterns,
        "other_events": other_events,
        "services_seen": seen,
        "services_missing": missing,
        "release_changes": release_changes,
        "cross_service_traces": cross_service_traces,
        "limits": limits,
        "read_gaps": gaps,
    }
