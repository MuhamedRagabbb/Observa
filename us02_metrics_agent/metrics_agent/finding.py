"""Turn a metrics profile into the Metrics Agent finding.

The LLM only interprets. Code attaches every number and evidence row, and a
strict validator rejects anything the profile does not support. If the model
is unavailable or keeps failing validation, the finding falls back to a
deterministic one built from the profile alone.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .llm import LLMError

CONFIDENCE = ("low", "medium", "high")
METRIC_FIELDS = (
    "id", "service", "rule", "category", "unit", "threshold",
    "trigger_value", "latest_value", "deviation", "severity", "trend",
    "window_end", "window_seconds"
)

SYSTEM_PROMPT = """You are the Metrics Agent of an incident investigation system.
You receive a deterministic profile of bounded metric observations and alert evaluations for one incident.

Rules:
1. Use only the facts and numbers provided. Never invent services, rules, metrics, or numbers.
2. Point at evidence only by id (M1, M2 ...). The system attaches the details.
3. You may quote a number only if it appears in the profile.
4. Metric deviations show symptoms and resource saturation, but do not prove root cause. Never use the phrase "root cause"; offer a hypothesis.
5. If resource_saturation (e.g. database pool pressure, pool timeouts) is present on a service, prioritize that service: resource exhaustion on that service directly explains downstream/upstream request errors and latency.
6. Compare trigger values with thresholds and note recovery or persistence trends.
7. Say what metrics cannot show (see limits).
8. The absence of metrics from a service is NOT evidence about that service; it only limits what can be concluded.

Answer with ONE JSON object and nothing else:
{"suspected_service": <one of affected_services, or null>,
 "hypothesis": "<1-3 sentences>",
 "evidence_ids": ["M1", "..."],
 "confidence": "low" | "medium" | "high",
 "caveats": ["..."]}"""


def make_view(profile: Dict[str, Any], incident: Dict[str, Any]) -> Dict[str, Any]:
    metrics = [
        {k: m.get(k) for k in METRIC_FIELDS}
        for m in profile.get("metrics", [])
    ]
    sats = [
        {k: m.get(k) for k in METRIC_FIELDS}
        for m in profile.get("resource_saturations", [])
    ]
    return {
        "incident": {
            "affected_services": incident.get("affected_services", []),
            "summary": incident.get("summary"),
        },
        "profile": {
            "sample": profile.get("sample", {}),
            "metrics": metrics,
            "resource_saturations": sats,
            "temporal_order": profile.get("temporal_order", []),
            "services_seen": profile.get("services_seen", []),
            "services_missing": profile.get("services_missing", []),
            "limits": profile.get("limits", []),
        },
    }


def _allowed_numbers(view: Dict[str, Any]) -> Set[float]:
    p = view["profile"]
    vals: Set[float] = set()

    def add(x: Any) -> None:
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            vals.add(float(x))

    for m in p.get("metrics", []):
        for k in ("threshold", "trigger_value", "latest_value", "deviation", "window_seconds"):
            add(m.get(k))
    for k in ("observations_count", "metrics_count"):
        add(p.get("sample", {}).get(k))
    return vals


_ID = re.compile(r"\bM\d+\b")
_NUM = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?")


def _bad_numbers(text: str, allowed: Set[float]) -> List[str]:
    return [n for n in _NUM.findall(_ID.sub("", text)) if float(n) not in allowed]


def extract_json(text: str) -> Dict[str, Any]:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    obj = json.loads(t[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON answer is not an object")
    return obj


def validate(obj: Dict[str, Any], view: Dict[str, Any]) -> List[str]:
    errs: List[str] = []
    affected = view["incident"]["affected_services"]
    known_ids = {m["id"] for m in view["profile"]["metrics"]}
    allowed = _allowed_numbers(view)

    for key in ("suspected_service", "hypothesis", "evidence_ids", "confidence", "caveats"):
        if key not in obj:
            errs.append(f"missing key: {key}")
    if errs:
        return errs

    svc = obj["suspected_service"]
    if svc is not None and svc not in affected:
        errs.append(f"suspected_service {svc!r} is not one of affected_services {affected}")

    ev = obj["evidence_ids"]
    if not isinstance(ev, list) or any(e not in known_ids for e in ev):
        errs.append(f"evidence_ids must be a list of known ids {sorted(known_ids)}")
    elif svc is not None and not ev:
        errs.append("a suspected_service needs at least one evidence id")

    if obj["confidence"] not in CONFIDENCE:
        errs.append(f"confidence must be one of {list(CONFIDENCE)}")

    texts = []
    hyp = obj["hypothesis"]
    if not isinstance(hyp, str) or not hyp.strip() or len(hyp) > 600:
        errs.append("hypothesis must be a non-empty string of at most 600 characters")
    else:
        texts.append(hyp)

    cav = obj["caveats"]
    if not isinstance(cav, list) or any(not isinstance(c, str) or len(c) > 300 for c in cav):
        errs.append("caveats must be a list of strings of at most 300 characters")
    else:
        texts.extend(cav)

    for t in texts:
        if re.search(r"root\s*cause", t, re.I):
            errs.append('do not use the phrase "root cause"; offer a hypothesis instead')
            break
        bad = _bad_numbers(t, allowed)
        if bad:
            errs.append(f"numbers not found in the profile: {sorted(set(bad))}")
            break
    return errs


def _cap_confidence(conf: str, profile: Dict[str, Any], suspected: Optional[str], caveats: List[str]) -> str:
    reasons = []
    if profile.get("services_missing"):
        reasons.append(f"missing metrics for services: {', '.join(profile['services_missing'])}")
    if suspected and suspected in profile.get("services_missing", []):
        reasons.append(f"there are no metric observations for suspected service {suspected}")
    if profile.get("sample", {}).get("observations_count", 0) < 4:
        reasons.append("small metric sample size")

    if reasons and CONFIDENCE.index(conf) > 1:
        caveats.append("confidence capped at medium: " + "; ".join(reasons))
        return "medium"
    return conf


def _evidence_rows(ids: List[str], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    index = {m["id"]: m for m in profile.get("metrics", [])}
    return [index[i] for i in ids if i in index]


def _fallback(profile: Dict[str, Any]) -> Tuple[Optional[str], str, List[str]]:
    sats = profile.get("resource_saturations", [])
    if sats:
        top = sats[0]
        svc = top["service"]
        hyp = (f"Observed resource saturation on {top['service']}: rule '{top['rule']}' reached "
               f"{top['trigger_value']} exceeding threshold {top['threshold']} {top['unit']}.")
        return svc, hyp, [top["id"]]

    metrics = profile.get("metrics", [])
    if not metrics:
        return None, "No metric observations found for this incident.", []

    top = max(metrics, key=lambda m: abs(m.get("deviation") or 0))
    svc = top["service"]
    hyp = (f"Observed metric deviation on {top['service']}: rule '{top['rule']}' reached "
           f"{top['trigger_value']} vs threshold {top['threshold']} {top['unit']}.")
    return svc, hyp, [top["id"]]


def _assemble(
    profile: Dict[str, Any],
    status: str,
    suspected: Optional[str],
    hypothesis: str,
    confidence: str,
    ids: List[str],
    caveats: List[str],
    validation: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "signal": "metrics",
        "incident_id": profile.get("incident_id"),
        "snapshot_id": profile.get("snapshot_id"),
        "status": status,
        "suspected_service": suspected,
        "hypothesis": hypothesis,
        "confidence": confidence,
        "evidence": _evidence_rows(ids, profile),
        "caveats": caveats,
        "limits": profile.get("limits", []),
        "root_cause": "not_determined",
        "validation": validation,
    }


def build_finding(profile: Dict[str, Any], incident: Dict[str, Any], llm: Any, max_attempts: int = 2) -> Dict[str, Any]:
    view = make_view(profile, incident)

    if profile.get("sample", {}).get("observations_count", 0) == 0:
        return _assemble(
            profile, "no_evidence", None,
            "There is no metric evidence for this incident, so metrics say nothing about it.",
            "low", [], [], {"passed": True, "attempts": 0, "errors": []}
        )

    fb_svc, fb_hyp, fb_ids = _fallback(profile)

    def fallback(status: str, attempts: int, errors: List[str]) -> Dict[str, Any]:
        return _assemble(
            profile, status, fb_svc, fb_hyp, "low", fb_ids, [],
            {"passed": False, "attempts": attempts, "errors": errors}
        )

    if llm is None:
        return fallback("llm_skipped", 0, [])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(view, indent=1, ensure_ascii=False)},
    ]
    errors: List[str] = []
    for attempt in range(1, max_attempts + 1):
        try:
            raw = llm.complete(messages)
        except LLMError as e:
            return fallback("llm_unavailable", attempt, [str(e)])
        try:
            obj = extract_json(raw)
            errors = validate(obj, view)
        except ValueError as e:
            obj, errors = None, [f"answer was not valid JSON: {e}"]
        if not errors:
            caveats = list(obj["caveats"])
            conf = _cap_confidence(obj["confidence"], profile, obj["suspected_service"], caveats)
            return _assemble(
                profile, "ok", obj["suspected_service"], obj["hypothesis"].strip(),
                conf, obj["evidence_ids"], caveats,
                {"passed": True, "attempts": attempt, "errors": []}
            )
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Your answer was rejected: " + "; ".join(errors)
             + ". Fix it and answer with the JSON object only."},
        ]
    return fallback("llm_rejected", max_attempts, errors)
