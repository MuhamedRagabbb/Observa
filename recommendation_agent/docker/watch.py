"""Watch completed F3 outputs and publish F4 recommendations once per input revision."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from tempfile import NamedTemporaryFile

from observa.f5_memory.service import IncidentMemory
from recommendation_agent import (
    F1ReportEvidenceResolver, RecommendationState, build_correlation_context,
    f5_memory_lookup, rank_recommendation, recommend_with_state, render_markdown,
    to_contract_result, validate_recommendation_evidence,
)

REPORTS = Path(os.getenv("F1_REPORTS_DIR", "/reports"))
RUNS = Path(os.getenv("F3_RUNS_DIR", "/runs"))
MEMORY = Path(os.getenv("OBSERVA_MEMORY_DB", "/memory/incident-memory-v3.sqlite3"))
USE_LLM = os.getenv("F4_USE_LLM", "1") == "1"
VALIDATION_REVISION = 2


def publish(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        stream.write(text)
    Path(stream.name).replace(path)


def process(source: Path) -> bool:
    data = source.read_bytes()
    payload = json.loads(data)
    folder = source.parent.parent.name
    iid = payload.get("incident_id", "")
    if folder != iid and not folder.startswith(iid + "-"):
        raise ValueError(f"F3 output identity mismatch: {source}")
    initial = source.parent / "output.initial.json"
    opening = source.name == "output.initial.json" or (initial.is_file() and data == initial.read_bytes())
    state_source = source.parent / ".state" / ("correlation.initial.json" if opening else "correlation.json")
    state_data = state_source.read_bytes() if state_source.is_file() else None
    revision = hashlib.sha256(data + (state_data or b"")).hexdigest()
    suffix = ".initial" if source.name == "output.initial.json" else ""
    target = source.parent.parent / "f4"
    stamp = target / f"input{suffix}.json"
    previous = json.loads(stamp.read_text(encoding="utf-8")) if stamp.is_file() else {}
    if previous.get("sha256") == revision and previous.get("validation_revision") == VALIDATION_REVISION:
        return False
    if opening and not suffix:
        prior = target / "input.initial.json"
        if not prior.is_file():
            return False  # Wait for the initial result; do not double the LLM requests.
        if json.loads(prior.read_text(encoding="utf-8")).get("sha256") == revision:
            for name in ("output.json", "output.md", "state.json"):
                source_name = name.replace(".", ".initial.", 1)
                publish(target / name, (target / source_name).read_text(encoding="utf-8"))
            publish(stamp, json.dumps({"sha256": revision, "f3_output": source.name,
                                       "validation_revision": VALIDATION_REVISION}) + "\n")
            return True
    state = json.loads(state_data) if state_data else None
    services = (state or {}).get("report", {}).get("history", {}).get("affected_services", [])
    mapping = {eid: row.get("service", "") for row in (state or {}).get("correlated_findings", []) for eid in row.get("evidence_ids", [])}
    resolver = F1ReportEvidenceResolver(REPORTS, payload["incident_id"],
                                        follow_up_dir=source.parent.parent / "f2" / "follow-up-evidence",
                                        fallback_services=services, service_by_evidence_id=mapping)
    saved = target / f"state{suffix}.json"
    if previous.get("sha256") == revision and saved.is_file():
        prior_state = RecommendationState.model_validate_json(saved.read_bytes())
        context = build_correlation_context(payload, f3_state=state)
        validations = validate_recommendation_evidence(prior_state.recommendation_options, context, resolver=resolver)
        ranked, top, details, status = rank_recommendation(prior_state.recommendation_options, validations, context)
        warnings = [w for w in prior_state.warnings if not w.startswith("No remediation action could be validated")]
        if status == "inconclusive" and context.status != "failed":
            warnings.append("No remediation action could be validated from current F3 evidence.")
        internal = prior_state.model_copy(update={
            "validation_results": validations, "ranked_recommendation_ids": ranked,
            "selected_recommendations": list(ranked), "top_recommendation_id": top,
            "ranking_details": details, "agent_status": status, "warnings": warnings,
        })
        result = to_contract_result(internal)
        publish(target / f"output{suffix}.json", result.model_dump_json(indent=2) + "\n")
        publish(target / f"output{suffix}.md", render_markdown(result, internal))
        publish(saved, internal.model_dump_json(indent=2) + "\n")
        publish(stamp, json.dumps({"sha256": revision, "f3_output": source.name,
                                   "validation_revision": VALIDATION_REVISION}) + "\n")
        print(f"[{payload['incident_id']}] F4 validation refreshed; status={status}", flush=True)
        return True
    try:
        memory = IncidentMemory(str(MEMORY), read_only=True)
        lookup = f5_memory_lookup(memory)
    except Exception as exc:
        print(f"[{payload['incident_id']}] F5 unavailable for F4: {exc}", flush=True)
        error = exc
        lookup = lambda _iid, _services: (_ for _ in ()).throw(error)
    result, internal = recommend_with_state(payload, f3_state=state, resolver=resolver,
                                            memory_lookup=lookup, use_llm=USE_LLM)
    publish(target / f"output{suffix}.json", result.model_dump_json(indent=2) + "\n")
    publish(target / f"output{suffix}.md", render_markdown(result, internal))
    publish(target / f"state{suffix}.json", internal.model_dump_json(indent=2) + "\n")
    publish(stamp, json.dumps({"sha256": revision, "f3_output": source.name,
                               "validation_revision": VALIDATION_REVISION}) + "\n")
    print(f"[{payload['incident_id']}] F4 status={result.status}; output={target / ('output'+suffix+'.json')}", flush=True)
    return True


_retry: dict[Path, tuple[int, float]] = {}


def scan() -> int:
    count = 0
    for source in sorted(RUNS.glob("INC-*/f3/output.initial.json")) + sorted(RUNS.glob("INC-*/f3/output.json")):
        attempts, retry_at = _retry.get(source, (0, 0.0))
        if time.monotonic() < retry_at:
            continue
        try:
            count += process(source)
            _retry.pop(source, None)
        except Exception as exc:
            attempts += 1
            delay = min(30 * (2 ** min(attempts - 1, 3)), 240)
            _retry[source] = (attempts, time.monotonic() + delay)
            print(f"F4 waiting on {source}: {type(exc).__name__}; retry in {delay}s", flush=True)
    return count


if __name__ == "__main__":
    while True:
        scan()
        time.sleep(5)
