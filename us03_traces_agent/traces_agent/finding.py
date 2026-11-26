"""Turn a traces profile into the Traces Agent finding.

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
PATTERN_FIELDS = (
    "id", "type", "service", "operation", "status", "count",
    "p95_duration_ms", "max_duration_ms", "is_deepest_origin"
)

SYSTEM_PROMPT = """You are the Traces Agent of an incident investigation system.
You receive a deterministic profile of bounded distributed traces and spans for one incident.

Rules:
1. Use only the facts and numbers provided. Never invent services, operations, status codes, or numbers.
2. Point at evidence only by id (T1, T2 ...). The system attaches the details.
3. You may quote a number only if it appears in the profile. Never quote raw trace/span hashes in the hypothesis.
4. Traces show request flow, call hierarchies, span durations, and error propagation paths. Traces do not prove root cause. Never use the phrase "root cause"; offer a hypothesis.
5. If deepest_failure_origin is identified in the profile, prioritize that service: failure originates at that leaf in the call tree and propagates upstream to callers.
6. Note slow operations and latency bottlenecks where relevant.
7. Say what traces cannot show (see limits).
8. The absence of traces from a service is NOT evidence about that service; it only limits what can be concluded.

Answer with ONE JSON object and nothing else:
{"suspected_service": <one of affected_services, or null>,
 "hypothesis": "<1-3 sentences>",
 "evidence_ids": ["T1", "..."],
 "confidence": "low" | "medium" | "high",
 "caveats": ["..."]}"""


def make_view(profile: Dict[str, Any], incident: Dict[str, Any]) -> Dict[str, Any]:
    patterns = [
        {k: p.get(k) for k in PATTERN_FIELDS}
        for p in profile.get("patterns", [])[:10]
    ]
    return {
        "incident": {
            "affected_services": incident.get("affected_services", []),
            "summary": incident.get("summary"),
        },
        "profile": {
            "sample": profile.get("sample", {}),
            "patterns": patterns,
            "deepest_failure_origin": profile.get("deepest_failure_origin"),
            "propagation_paths": profile.get("propagation_paths", []),
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

    for pat in p.get("patterns", []):
        for k in ("status", "count", "p95_duration_ms", "max_duration_ms"):
            add(pat.get(k))
    for k in ("traces_count", "spans_count", "failed_spans_count"):
        add(p.get("sample", {}).get(k))
    return vals


_ID = re.compile(r"\bT\d+\b")
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
    known_ids = {p["id"] for p in view["profile"]["patterns"]}
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
        reasons.append(f"missing traces for services: {', '.join(profile['services_missing'])}")
    if suspected and suspected in profile.get("services_missing", []):
        reasons.append(f"there are no traces for suspected service {suspected}")
    if profile.get("sample", {}).get("traces_count", 0) < 3:
        reasons.append("small trace sample size (< 3)")

    if reasons and CONFIDENCE.index(conf) > 1:
        caveats.append("confidence capped at medium: " + "; ".join(reasons))
        return "medium"
    return conf


def _evidence_rows(ids: List[str], profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    index = {p["id"]: p for p in profile.get("patterns", [])}
    return [index[i] for i in ids if i in index]


def _fallback(profile: Dict[str, Any]) -> Tuple[Optional[str], str, List[str]]:
    origin = profile.get("deepest_failure_origin")
    pats = profile.get("patterns", [])
    if origin and pats:
        match = next((p for p in pats if p.get("is_deepest_origin")), pats[0])
        st_text = f" (HTTP {match['status']})" if match.get("status") else ""
        hyp = (f"Distributed traces show failure propagation originating at {origin} during '{match['operation']}'"
               f"{st_text}, propagating upstream to caller services.")
        return origin, hyp, [match["id"]]

    if pats:
        top = pats[0]
        st_text = f" (HTTP {top['status']})" if top.get("status") else ""
        hyp = f"Distributed traces observed {top['count']} '{top['type']}' spans on {top['service']} during '{top['operation']}'{st_text}."
        return top["service"], hyp, [top["id"]]

    return None, "No failing or slow spans identified in the retained trace sample.", []


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
        "signal": "traces",
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

    if profile.get("sample", {}).get("traces_count", 0) == 0:
        return _assemble(
            profile, "no_evidence", None,
            "There is no trace evidence for this incident, so traces say nothing about it.",
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
