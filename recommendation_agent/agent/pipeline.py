"""End-to-end F4 pipeline producing RecommendationResult for F7 Report."""
from __future__ import annotations

from html import escape
import re
from typing import Any

from .contracts import (
    ContractRecommendationOption,
    ErrorEnvelope,
    F3CorrelationInput,
    IncidentWindow,
    RecommendationResult,
    RecommendationState,
    build_correlation_context,
)
from .evidence import EvidenceResolver
from .generator import generate_recommendation_options
from .llm import LLMClient, ScriptedLLM
from .memory import MemoryLookup, search_approved_incidents
from .ranker import rank_recommendation
from .validator import validate_recommendation_evidence


def to_contract_result(state: RecommendationState) -> RecommendationResult:
    """Convert internal RecommendationState to the public F4 -> F7 RecommendationResult contract."""
    val_by_id = {v.recommendation_id: v for v in state.validation_results}
    options = []
    for opt in state.recommendation_options:
        record = val_by_id[opt.recommendation_id]
        options.append(
            ContractRecommendationOption(
                recommendation_id=opt.recommendation_id,
                action=opt.action,
                action_type=opt.action_type,
                evidence_links=record.evidence_links,
                risk_or_caveat=opt.risk_or_caveat,
                validation=record.validation,
                rationale=opt.rationale,
            )
        )
    return RecommendationResult(
        incident_id=state.incident_id,
        correlation_run_id=state.correlation_run_id,
        recommendation_options=options,
        ranked_recommendation_ids=list(state.ranked_recommendation_ids),
        top_recommendation_id=state.top_recommendation_id,
        status=state.agent_status,
        error=state.error,
    )


def recommend_with_state(
    correlation: F3CorrelationInput | dict[str, Any],
    *,
    f3_state: dict[str, Any] | None = None,
    resolver: EvidenceResolver | None = None,
    window: IncidentWindow | None = None,
    memory_lookup: MemoryLookup | None = None,
    llm: LLMClient | ScriptedLLM | None = None,
    use_llm: bool = True,
) -> tuple[RecommendationResult, RecommendationState]:
    """Run F4-US-01 -> F4-US-02 -> F4-US-03 and return both F7 contract and internal state."""
    context = build_correlation_context(correlation, f3_state=f3_state, window=window)
    if context.status == "failed":
        err = context.error or ErrorEnvelope(
            code="UPSTREAM_CORRELATION_FAILED",
            message="F3 correlation failed; F4 cannot generate recommendations.",
            retryable=False,
            operation_id=f"REC-{context.correlation_run_id}",
            details=None,
        )
        state = RecommendationState(
            incident_id=context.incident_id,
            correlation_run_id=context.correlation_run_id,
            agent_status="failed",
            warnings=["Upstream F3 correlation status is failed."],
            error=err,
        )
        return to_contract_result(state), state

    memory = search_approved_incidents(context, memory_lookup)
    warnings: list[str] = []
    if memory.status == "failed":
        warnings.append(
            f"F5 memory lookup failed ({memory.error.message if memory.error else 'unavailable'}); "
            "continuing with current incident evidence only."
        )

    options = generate_recommendation_options(
        context,
        memory=memory,
        llm=llm,
        use_llm=use_llm,
    )
    validations = validate_recommendation_evidence(options, context, resolver=resolver)
    ranked_ids, top_id, details, status = rank_recommendation(options, validations, context)

    if status == "inconclusive":
        warnings.append("No remediation action could be validated from current F3 evidence.")

    state = RecommendationState(
        incident_id=context.incident_id,
        correlation_run_id=context.correlation_run_id,
        recommendation_options=options,
        validation_results=validations,
        ranked_recommendation_ids=ranked_ids,
        selected_recommendations=list(ranked_ids),
        top_recommendation_id=top_id,
        ranking_details=details,
        memory_status=memory.status,
        memory_items_count=len(memory.items),
        agent_status=status,
        warnings=warnings,
    )
    return to_contract_result(state), state


def recommend(
    correlation: F3CorrelationInput | dict[str, Any],
    *,
    f3_state: dict[str, Any] | None = None,
    resolver: EvidenceResolver | None = None,
    window: IncidentWindow | None = None,
    memory_lookup: MemoryLookup | None = None,
    llm: LLMClient | ScriptedLLM | None = None,
    use_llm: bool = True,
) -> RecommendationResult:
    """Run F4 and return the public RecommendationResult contract for F7."""
    result, _ = recommend_with_state(
        correlation,
        f3_state=f3_state,
        resolver=resolver,
        window=window,
        memory_lookup=memory_lookup,
        llm=llm,
        use_llm=use_llm,
    )
    return result


def failed_contract_result(
    incident_id: str,
    correlation_run_id: str,
    message: str,
    *,
    retryable: bool,
    error_type: str,
    status_code: int | None = None,
) -> RecommendationResult:
    """Build a typed failed RecommendationResult without silent fallback."""
    return RecommendationResult(
        incident_id=incident_id,
        correlation_run_id=correlation_run_id,
        recommendation_options=[],
        ranked_recommendation_ids=[],
        top_recommendation_id=None,
        status="failed",
        error=ErrorEnvelope(
            code="RECOMMENDATION_FAILED",
            message=" ".join(message.split())[:600],
            retryable=retryable,
            operation_id=f"REC-{correlation_run_id}",
            details={"error_type": error_type, "status_code": status_code},
        ),
    )


def render_markdown(result: RecommendationResult, state: RecommendationState | None = None) -> str:
    """Render F4 recommendation output for inspection (not an F7 incident report)."""
    def plain(value: str) -> str:
        text = escape(" ".join(value.splitlines()), quote=False)
        return re.sub(r"([\\`*_\[\]~|])", r"\\\1", text)

    def code(value: str) -> str:
        fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", value)), default=0))
        return f"{fence} {' '.join(value.splitlines())} {fence}"

    lines = [
        f"# Recommendations · {plain(result.incident_id)}",
        "",
        f"**Correlation Run** {code(result.correlation_run_id)}",
        "",
        f"**Status** {result.status.capitalize()}",
        "",
        f"**Top Recommendation** {code(result.top_recommendation_id) if result.top_recommendation_id else 'None'}",
    ]
    if result.error:
        lines.extend(["", "## Error", "", f"**{plain(result.error.code)}** · {plain(result.error.message)}"])
        return "\n".join(lines) + "\n"

    options_by_id = {opt.recommendation_id: opt for opt in result.recommendation_options}
    rank_by_id = {d.recommendation_id: d for d in (state.ranking_details if state else [])}
    val_by_id = {v.recommendation_id: v for v in (state.validation_results if state else [])}

    lines.extend(["", "## Ranked Recommendations", ""])
    if not result.ranked_recommendation_ids:
        lines.append("No validated recommendations are currently ranked.")
    else:
        for idx, rec_id in enumerate(result.ranked_recommendation_ids, 1):
            opt = options_by_id[rec_id]
            lines.append(
                f"{idx}. **{opt.action_type}** ({opt.validation}) · {code(rec_id)} — {plain(opt.action)}"
            )
            if opt.risk_or_caveat:
                lines.append(f"   Caveat: {plain(opt.risk_or_caveat)}")
            detail = rank_by_id.get(rec_id)
            if detail:
                lines.append(f"   Ranking: {plain(detail.explanation)}")

    unranked = [opt for opt in result.recommendation_options if opt.recommendation_id not in result.ranked_recommendation_ids]
    if unranked:
        lines.extend(["", "## Unranked / Unsupported Options", ""])
        for opt in unranked:
            lines.append(f"- **{opt.action_type}** ({opt.validation}) · {code(opt.recommendation_id)} — {plain(opt.action)}")
            rec_val = val_by_id.get(opt.recommendation_id)
            if rec_val and rec_val.reasons:
                lines.append(f"  Reasons: {plain('; '.join(rec_val.reasons))}")

    lines.extend(["", "## All Options & Evidence Validation", ""])
    if not result.recommendation_options:
        lines.append("No options generated.")
    for opt in result.recommendation_options:
        lines.extend([f"### {code(opt.recommendation_id)} · {opt.action_type} ({opt.validation})", ""])
        lines.append(plain(opt.action))
        lines.append("")
        lines.append(f"Rationale: {plain(opt.rationale)}")
        if opt.risk_or_caveat:
            lines.append(f"Risk/Caveat: {plain(opt.risk_or_caveat)}")
        if opt.evidence_links:
            lines.append("")
            lines.append("**Evidence Links**")
            for link in opt.evidence_links:
                lines.append(
                    f"- {code(link.evidence_id)} · relation={link.relation}, "
                    f"reference_valid={link.reference_valid}, relevance={link.relevance_status}, "
                    f"support={link.support_status}"
                )
    return "\n".join(lines) + "\n"
