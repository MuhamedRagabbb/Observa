"""Deterministic profiler for the saved metrics evidence of one OBSERVA incident.

No LLM in this file. Anything the agent later says about metrics must be
traceable to this output.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def classify_rule(rule: str) -> str:
    """resource_saturation | latency | error_rate | business_decline | other"""
    r = (rule or "").lower()
    if any(k in r for k in ("pool", "cpu", "memory", "disk", "saturation", "timeout")):
        return "resource_saturation"
    if any(k in r for k in ("latency", "duration", "slow")):
        return "latency"
    if any(k in r for k in ("http_errors", "dependency_errors", "error")):
        return "error_rate"
    if any(k in r for k in ("payment_failures", "decline")):
        return "business_decline"
    return "other"


def _severity(category: str, unit: str, val: float, thresh: float) -> str:
    if val is None or thresh is None:
        return "unknown"
    if category == "resource_saturation":
        return "critical" if val >= thresh else "normal"
    if unit == "ratio":
        if val >= 0.5:
            return "critical"
        return "warning" if val >= thresh else "normal"
    if val >= thresh * 1.5:
        return "critical"
    return "warning" if val >= thresh else "normal"


def _trend(trigger_val: float, latest_val: float, thresh: float) -> str:
    if trigger_val is None or latest_val is None:
        return "unknown"
    if trigger_val >= thresh and latest_val < thresh:
        return "recovered"
    if latest_val < trigger_val:
        return "improving"
    if latest_val > trigger_val:
        return "worsening"
    return "stable"


def profile_metrics(
    observations: List[Dict[str, Any]],
    incident: Dict[str, Any],
    read_gaps: Optional[List[str]] = None,
    query_window: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    gaps = list(read_gaps or [])

    # Group by (service, rule)
    by_key: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for obs in observations:
        svc = obs.get("service") or "unknown"
        rule = obs.get("rule") or "unknown"
        by_key[(svc, rule)].append(obs)

    metrics_list: List[Dict[str, Any]] = []
    resource_saturations: List[Dict[str, Any]] = []

    for idx, ((svc, rule), obs_list) in enumerate(sorted(by_key.items()), 1):
        trigger_obs = next((o for o in obs_list if o.get("phase") == "trigger"), obs_list[0])
        latest_obs = next((o for o in obs_list if o.get("phase") == "latest"), obs_list[-1])

        cat = classify_rule(rule)
        unit = trigger_obs.get("unit") or "unknown"
        thresh = trigger_obs.get("threshold")
        t_val = trigger_obs.get("value")
        l_val = latest_obs.get("value")

        t_val_f = float(t_val) if t_val is not None else None
        l_val_f = float(l_val) if l_val is not None else None
        thresh_f = float(thresh) if thresh is not None else None

        dev = round(t_val_f - thresh_f, 4) if (t_val_f is not None and thresh_f is not None) else None
        sev = _severity(cat, unit, t_val_f, thresh_f) if (t_val_f is not None and thresh_f is not None) else "unknown"
        tr = _trend(t_val_f, l_val_f, thresh_f) if (t_val_f is not None and thresh_f is not None) else "unknown"

        ev = trigger_obs.get("evidence") or {}
        w_end = ev.get("window_end")
        w_sec = ev.get("window_seconds")
        q = ev.get("query")
        req_count = ev.get("request_count")

        item = {
            "id": f"M{idx}",
            "service": svc,
            "rule": rule,
            "category": cat,
            "unit": unit,
            "threshold": thresh_f,
            "trigger_value": t_val_f,
            "latest_value": l_val_f,
            "deviation": dev,
            "severity": sev,
            "trend": tr,
            "alert_id": trigger_obs.get("alert_id"),
            "window_end": w_end,
            "window_seconds": w_sec,
            "query": q,
            "request_count": req_count,
        }
        metrics_list.append(item)
        if cat == "resource_saturation" and t_val_f is not None and thresh_f is not None and t_val_f >= thresh_f:
            resource_saturations.append(item)

    seen = sorted({m["service"] for m in metrics_list if m["service"] != "unknown"})
    affected = incident.get("affected_services", [])
    missing = [s for s in affected if s not in seen]

    # Temporal ordering based on window_end
    temporal_order = sorted(
        [{"id": m["id"], "service": m["service"], "rule": m["rule"], "window_end": m["window_end"]}
         for m in metrics_list if m.get("window_end")],
        key=lambda x: x["window_end"]
    )

    limits: List[str] = []
    if not observations:
        limits.append("metric evidence could not be read" if gaps else "no metric evidence in this snapshot")
    else:
        limits.append(
            f"retained sample of {len(observations)} observations across {len(seen)} services; "
            "metrics reflect alert evaluation windows and queries, not full continuous history"
        )
    for s in missing:
        limits.append(f"no metric observations for affected service: {s}")
    limits.extend(gaps)

    return {
        "sample": {
            "observations_count": len(observations),
            "metrics_count": len(metrics_list),
            "query_window": query_window,
            "coverage": (metadata or {}).get("coverage"),
        },
        "metrics": metrics_list,
        "resource_saturations": resource_saturations,
        "temporal_order": temporal_order,
        "services_seen": seen,
        "services_missing": missing,
        "limits": limits,
        "read_gaps": gaps,
    }
