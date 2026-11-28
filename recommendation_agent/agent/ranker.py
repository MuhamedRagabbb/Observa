"""F4-US-03: Deterministic ranking and selection of validated recommendations."""
from __future__ import annotations

from typing import Literal

from .contracts import (
    CorrelationContext,
    ProposedOption,
    RankedOptionDetail,
    ValidationRecord,
)

# Documented F4-US-03 ranking weights:
#   Validation tier: supported (+100) > conditional (+50); unsupported excluded.
#   Action priority: when a leading cause is established, remediation (+30) > verification (+20)
#     > alternative remediation (+15) > investigation (+10). When inconclusive, verification (+25) > investigation (+20).
#   Evidence strength: +0..20 proportional to valid supporting citations + signal diversity (+0..6).
#   Urgency bonus: critical (+12), high (+8), medium (+4), low (+0).
#   Risk penalty: low (0), medium (-5), high (-12).
#   Uncertainty penalty: -6 when candidate carries explicit uncertainty or F3 status is partial.
VALIDATION_WEIGHT = {"supported": 100.0, "conditional": 50.0}
URGENCY_WEIGHT = {"critical": 12.0, "high": 8.0, "medium": 4.0, "low": 0.0}
RISK_PENALTY = {"low": 0.0, "medium": 5.0, "high": 12.0}
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
ACTION_ORDER_WITH_CAUSE = {"remediation": 0, "verification": 1, "investigation": 2}
ACTION_ORDER_INCONCLUSIVE = {"verification": 0, "investigation": 1, "remediation": 2}


def _distinct_signal_count(record: ValidationRecord) -> int:
    signals = set()
    for link in record.evidence_links:
        if link.support_status == "supported" and link.reference_valid is True and link.relevance_status == "relevant":
            parts = link.evidence_id.split("/", 2)
            if len(parts) == 3:
                sig = parts[2].partition("#")[0]
                if sig in ("logs", "metrics", "traces"):
                    signals.add(sig)
    return len(signals)


def _overall_status(
    context: CorrelationContext,
    ranked_records: list[ValidationRecord],
) -> Literal["completed", "partial", "inconclusive", "failed"]:
    if context.status == "failed":
        return "failed"
    validated_remediations = [r for r in ranked_records if r.action_type == "remediation"]
    if context.status == "inconclusive" or context.leading_candidate_id is None or not validated_remediations:
        return "inconclusive"
    if context.status == "partial" or not any(r.validation == "supported" for r in validated_remediations):
        return "partial"
    return "completed"


def rank_recommendation(
    options: list[ProposedOption],
    validations: list[ValidationRecord],
    context: CorrelationContext,
) -> tuple[list[str], str | None, list[RankedOptionDetail], Literal["completed", "partial", "inconclusive", "failed"]]:
    """Rank supported and conditional recommendations deterministically.

    Unsupported options are never placed in ranked_recommendation_ids.
    """
    if context.status == "failed":
        return [], None, [], "failed"

    val_by_id = {v.recommendation_id: v for v in validations}
    candidates_by_id = {c.candidate_id: c for c in context.root_cause_candidates}
    inconclusive = context.status == "inconclusive" or context.leading_candidate_id is None

    scored: list[tuple[tuple, ProposedOption, ValidationRecord, float, str]] = []
    ranked_records: list[ValidationRecord] = []

    for option in options:
        record = val_by_id.get(option.recommendation_id)
        if record is None or record.validation == "unsupported":
            continue
        ranked_records.append(record)

        is_leading = option.candidate_id == context.leading_candidate_id and context.leading_candidate_id is not None
        if inconclusive:
            action_weight = 25.0 if option.action_type == "verification" else 20.0
            action_tie = ACTION_ORDER_INCONCLUSIVE[option.action_type]
        else:
            if option.action_type == "remediation":
                action_weight = 30.0 if is_leading else 15.0
            elif option.action_type == "verification":
                action_weight = 20.0
            else:
                action_weight = 10.0
            action_tie = ACTION_ORDER_WITH_CAUSE[option.action_type]

        cand = candidates_by_id.get(option.candidate_id) if option.candidate_id else None
        has_uncertainty = bool(cand and cand.uncertainty) or context.status == "partial"
        signal_diversity = _distinct_signal_count(record)
        score = (
            VALIDATION_WEIGHT[record.validation]
            + action_weight
            + (record.support_strength * 20.0)
            + (signal_diversity * 3.0)
            + URGENCY_WEIGHT[option.urgency]
            - RISK_PENALTY[option.risk_level]
            - (6.0 if has_uncertainty else 0.0)
        )
        score = round(score, 4)

        explanation = (
            f"validation={record.validation}, type={option.action_type}, "
            f"support_strength={record.support_strength:.2f} (signals={signal_diversity}), "
            f"urgency={option.urgency}, risk={option.risk_level}"
            + (", uncertainty_penalty=applied" if has_uncertainty else "")
            + f", score={score:.2f}"
        )
        sort_key = (
            -score,
            0 if record.validation == "supported" else 1,
            action_tie,
            RISK_ORDER[option.risk_level],
            option.recommendation_id,
        )
        scored.append((sort_key, option, record, score, explanation))

    scored.sort(key=lambda item: item[0])
    ranked_ids = [item[1].recommendation_id for item in scored]
    top_id = ranked_ids[0] if ranked_ids else None
    details = [
        RankedOptionDetail(
            recommendation_id=opt.recommendation_id,
            rank=idx,
            score=score,
            validation=rec.validation,
            action_type=opt.action_type,
            explanation=explanation,
        )
        for idx, (_, opt, rec, score, explanation) in enumerate(scored, 1)
    ]
    overall_status = _overall_status(context, ranked_records)
    return ranked_ids, top_id, details, overall_status
