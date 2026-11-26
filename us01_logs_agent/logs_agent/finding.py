"""Turn a log profile into the Logs Agent finding.

The LLM only interprets. Code attaches every number and evidence row, and a
strict validator rejects anything the profile does not support. If the model
is unavailable or keeps failing validation, the finding falls back to a
deterministic one built from the profile alone.
"""
from __future__ import annotations

import json
import re

from .llm import LLMError

CONFIDENCE = ("low", "medium", "high")
PATTERN_FIELDS = ("category", "service", "event", "error_type", "status", "route",
                  "count", "window_seconds", "rate_per_s", "p95_duration_ms")
OTHER_FIELDS = ("service", "event", "level", "count")

SYSTEM_PROMPT = """You are the Logs Agent of an incident investigation system.
You receive a deterministic profile of a bounded SAMPLE of one incident's logs.

Rules:
1. Use only the facts provided. Never invent services, events, error types or numbers.
2. Point at evidence only by id (P1, P2, E1 ...). The system attaches the details.
3. You may quote a number only if it appears in the profile. Never quote timestamps.
4. Counts from patterns with different windows are not comparable; compare rates, never raw counts.
5. Log patterns do not prove causation. Never state a root cause and never use the phrase "root cause"; offer a hypothesis.
6. Say what the logs cannot show (see limits).
7. The absence of logs from a service is NOT evidence about that service. Never use it to support a hypothesis; it only limits what can be concluded.
8. Prefer direct links: an error_type that names a dependency, and cross_service_traces above zero (the same request failing in two services). Do not use words like "surge" or "spike": the sample has no baseline.

Answer with ONE JSON object and nothing else:
{"suspected_service": <one of affected_services, or null>,
 "hypothesis": "<1-3 sentences>",
 "evidence_ids": ["P1", "..."],
 "confidence": "low" | "medium" | "high",
 "caveats": ["..."]}"""


def make_view(profile: dict, incident: dict) -> dict:
    patterns = [{"id": f"P{i}", **{k: p.get(k) for k in PATTERN_FIELDS}}
                for i, p in enumerate(profile["patterns"], 1)]
    others = [{"id": f"E{i}", **{k: e.get(k) for k in OTHER_FIELDS}}
              for i, e in enumerate(profile.get("other_events", []), 1)]
    calls, seen = [], set()
    for r in incident.get("relationships", []):
        d = r.get("direction")
        if d and (d["upstream"], d["downstream"]) not in seen:
            seen.add((d["upstream"], d["downstream"]))
            calls.append({"upstream": d["upstream"], "downstream": d["downstream"],
                          "kind": r.get("kind"), "confidence": r.get("confidence")})
    s = profile["sample"]
    return {
        "incident": {
            "affected_services": incident.get("affected_services", []),
            "anchor": incident.get("anchor"),
            "dependency_calls": calls,
        },
        "profile": {
            "sample": {"records": s["records"], "covers_incident_start": s["covers_incident_start"]},
            "categories": profile["categories"],
            "patterns": patterns,
            "other_events": others,
            "services_seen": profile["services_seen"],
            "services_missing": profile["services_missing"],
            "release_changes": profile["release_changes"],
            "cross_service_traces": profile["cross_service_traces"],
            "limits": profile["limits"],
        },
    }


def _allowed_numbers(view: dict) -> set[float]:
    p = view["profile"]
    vals: set[float] = set()

    def add(x):
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            vals.add(float(x))

    for row in p["patterns"]:
        for k in ("count", "status", "window_seconds", "rate_per_s", "p95_duration_ms"):
            add(row.get(k))
    for row in p["other_events"]:
        add(row.get("count"))
    add(p["sample"]["records"])
    add(p["cross_service_traces"])
    for v in p["categories"].values():
        add(v)
    return vals


_ID = re.compile(r"\b[PE]\d+\b")
_NUM = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?")


def _bad_numbers(text: str, allowed: set[float]) -> list[str]:
    return [n for n in _NUM.findall(_ID.sub("", text)) if float(n) not in allowed]


def extract_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found")
    obj = json.loads(t[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("JSON answer is not an object")
    return obj


def validate(obj: dict, view: dict) -> list[str]:
    errs: list[str] = []
    affected = view["incident"]["affected_services"]
    ids = {x["id"] for x in view["profile"]["patterns"]} | {x["id"] for x in view["profile"]["other_events"]}
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
    if not isinstance(ev, list) or any(e not in ids for e in ev):
        errs.append(f"evidence_ids must be a list of known ids {sorted(ids)}")
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
    for t in texts:
        bad = _bad_numbers(t, allowed)
        if bad:
            errs.append(f"numbers not found in the profile: {sorted(set(bad))}")
            break
    return errs


def _cap_confidence(conf: str, profile: dict, suspected, caveats: list[str]) -> str:
    reasons = []
    if profile["sample"].get("covers_incident_start") is False:
        reasons.append("the sample does not cover the incident start")
    if suspected and suspected in profile.get("services_missing", []):
        reasons.append(f"there are no log records from {suspected}")
    if reasons and CONFIDENCE.index(conf) > 1:
        caveats.append("confidence capped at medium: " + "; ".join(reasons))
        return "medium"
    return conf


def _evidence_rows(ids: list[str], view: dict) -> list[dict]:
    index = {x["id"]: x for x in view["profile"]["patterns"] + view["profile"]["other_events"]}
    return [index[i] for i in ids if i in index]


def _fallback(view: dict) -> tuple[str, list[str]]:
    pats = view["profile"]["patterns"]
    if not pats:
        return "No failure pattern was found in the log sample.", []
    top = pats[0]
    et = f" ({top['error_type']})" if top.get("error_type") else ""
    return (f"Most frequent failure pattern in the sample: {top['service']} {top['event']}{et}. "
            "This is a description, not a diagnosis."), [top["id"]]


def _assemble(profile, view, status, suspected, hypothesis, confidence, ids, caveats, validation):
    return {
        "signal": "logs",
        "incident_id": profile.get("incident_id"),
        "snapshot_id": profile.get("snapshot_id"),
        "status": status,
        "suspected_service": suspected,
        "hypothesis": hypothesis,
        "confidence": confidence,
        "evidence": _evidence_rows(ids, view),
        "caveats": caveats,
        "limits": profile["limits"],
        "root_cause": "not_determined",
        "validation": validation,
    }


def build_finding(profile: dict, incident: dict, llm, max_attempts: int = 2) -> dict:
    view = make_view(profile, incident)

    if profile["sample"]["records"] == 0:
        return _assemble(profile, view, "no_evidence", None,
                         "There is no log evidence for this incident, so the logs say nothing about it.",
                         "low", [], [], {"passed": True, "attempts": 0, "errors": []})

    fb_text, fb_ids = _fallback(view)

    def fallback(status, attempts, errors):
        return _assemble(profile, view, status, None, fb_text, "low", fb_ids, [],
                         {"passed": False, "attempts": attempts, "errors": errors})

    if llm is None:
        return fallback("llm_skipped", 0, [])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(view, indent=1, ensure_ascii=False)},
    ]
    errors: list[str] = []
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
            return _assemble(profile, view, "ok", obj["suspected_service"], obj["hypothesis"].strip(),
                             conf, obj["evidence_ids"], caveats,
                             {"passed": True, "attempts": attempt, "errors": []})
        messages += [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": "Your answer was rejected: " + "; ".join(errors)
             + ". Fix it and answer with the JSON object only."},
        ]
    return fallback("llm_rejected", max_attempts, errors)
