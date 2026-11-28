"""CLI entry point for standalone F4 recommendation generation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import (
    F1ReportEvidenceResolver,
    MemorySearchResult,
    RecommendationResult,
    recommend_with_state,
    render_markdown,
)


def _write_atomic(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", dir=target.parent, delete=False, encoding="utf-8") as stream:
            tmp = Path(stream.name)
            stream.write(text)
        tmp.replace(target)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate, validate, and rank F4 recommendations from an F3 CorrelationResult JSON."
    )
    parser.add_argument("correlation", type=Path, help="Path to F3 public output.json (or saved F4 result with --from-result).")
    parser.add_argument("--f3-state", type=Path, default=None, help="Optional F3 internal state JSON (correlation.json) for richer observations and incident window.")
    parser.add_argument("--reports-dir", type=Path, default=None, help="Optional F1 reports directory for live evidence ref resolution.")
    parser.add_argument("--follow-up-dir", type=Path, default=None, help="Optional F2 follow-up-evidence directory.")
    parser.add_argument("--memory", type=Path, default=None, help="Optional F5 MemorySearchResult JSON file.")
    parser.add_argument("--no-llm", action="store_true", help="Use deterministic rule-based option generation without calling an LLM.")
    parser.add_argument("--out", type=Path, default=None, help="Save RecommendationResult JSON (.json) or Markdown (.md).")
    parser.add_argument("--state-out", type=Path, default=None, help="Save full internal RecommendationState JSON.")
    parser.add_argument("--from-result", action="store_true", help="Render an existing RecommendationResult JSON as Markdown offline.")
    args = parser.parse_args()

    try:
        data = json.loads(args.correlation.read_text(encoding="utf-8-sig"))
        if args.from_result:
            result = RecommendationResult.model_validate(data)
            state = None
        else:
            f3_state = json.loads(args.f3_state.read_text(encoding="utf-8-sig")) if args.f3_state else None
            resolver = None
            if args.reports_dir:
                incident_id = str(data.get("incident_id") or "")
                services = []
                service_map = {}
                if f3_state:
                    services = list((f3_state.get("report") or {}).get("history", {}).get("affected_services", []))
                    for obs in f3_state.get("correlated_findings") or []:
                        for eid in obs.get("evidence_ids") or []:
                            service_map[eid] = obs.get("service", "")
                resolver = F1ReportEvidenceResolver(
                    args.reports_dir,
                    incident_id,
                    follow_up_dir=args.follow_up_dir,
                    fallback_services=services,
                    service_by_evidence_id=service_map,
                )
            memory_lookup = None
            if args.memory:
                mem_payload = MemorySearchResult.model_validate_json(args.memory.read_bytes())
                memory_lookup = lambda _iid, _svcs: mem_payload
            result, state = recommend_with_state(
                data,
                f3_state=f3_state,
                resolver=resolver,
                memory_lookup=memory_lookup,
                use_llm=not args.no_llm,
            )
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Invalid input: {exc}\n")
    except RuntimeError as exc:
        parser.exit(1, f"Recommendation failed: {exc}\n")

    if args.state_out and state is not None:
        _write_atomic(args.state_out, state.model_dump_json(indent=2) + "\n")

    if args.out:
        if args.out.suffix.lower() == ".md":
            _write_atomic(args.out, render_markdown(result, state))
        else:
            _write_atomic(args.out, result.model_dump_json(indent=2) + "\n")

    if args.from_result and args.out is None:
        print(render_markdown(result), end="")
    elif not args.from_result:
        print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
