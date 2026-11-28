"""F4-US-02: Deterministic evidence validation for proposed recommendation options."""
from __future__ import annotations

from datetime import datetime
import re

from .contracts import (
    CorrelationCandidateInput,
    CorrelationContext,
    EvidenceCheckDetail,
    EvidenceLink,
    EvidenceRef,
    ProposedOption,
    ValidationRecord,
    ValidationStatus,
)
from .evidence import EvidenceResolver


def _windows_overlap(
    ref_start: datetime,
    ref_end: datetime | None,
    win_start: datetime,
    win_end: datetime | None,
) -> bool:
    if win_end is not None and ref_start >= win_end:
        return False
    if ref_end is not None:
        if ref_end <= win_start:
            return False
        return True
    return ref_start >= win_start


def _service_matches(
    ref_service: str,
    option: ProposedOption,
    candidate: CorrelationCandidateInput | None,
    context: CorrelationContext,
    has_candidate_link: bool,
) -> bool:
    if ref_service == "unknown":
        return bool(context.affected_services)
    target = option.target_service or (candidate.service if candidate else None)
    if target:
        if ref_service == target:
            return True
        # Allow directly connected dependency evidence only if F3 linked it to the candidate
        # or if this is an investigation/verification step spanning affected services.
        is_neighbor = any(
            (caller == target and callee == ref_service) or (callee == target and caller == ref_service)
            for caller, callee in context.dependencies
        )
        if is_neighbor and (has_candidate_link or option.action_type != "remediation"):
            return True
        if option.action_type in ("verification", "investigation") and ref_service in context.affected_services:
            return has_candidate_link or option.target_service is None
        return False
    if context.affected_services:
        return ref_service in context.affected_services
    return True


def _index_f3_links(
    context: CorrelationContext,
) -> tuple[dict[str, EvidenceLink], dict[str, set[str]], dict[str, list[EvidenceLink]]]:
    by_eid: dict[str, EvidenceLink] = {}
    obs_services: dict[str, set[str]] = {}
    cand_contradictions: dict[str, list[EvidenceLink]] = {}

    for obs in context.observations:
        for eid in obs.evidence_ids:
            obs_services.setdefault(eid, set()).add(obs.service)
            if eid not in by_eid:
                by_eid[eid] = EvidenceLink(
                    evidence_id=eid,
                    relation=obs.relation,
                    reference_valid=True,
                    relevance_status="relevant",
                    support_status=obs.support_status,
                )

    for cand in context.root_cause_candidates:
        c_contra: list[EvidenceLink] = []
        for link in cand.evidence_links:
            if link.relation == "contradicts" or link.support_status == "contradicted":
                c_contra.append(link)
            existing = by_eid.get(link.evidence_id)
            if existing is None or link.support_status == "contradicted":
                by_eid[link.evidence_id] = link
        cand_contradictions[cand.candidate_id] = c_contra

    return by_eid, obs_services, cand_contradictions


def _unsupported_external_gateway_fix(option: ProposedOption, context: CorrelationContext) -> bool:
    """Do not infer a gateway timeout from service-level 503s alone."""
    action = option.action.lower()
    if not re.search(r"(?:external|downstream|third.party).{0,32}gateway|gateway.{0,32}(?:external|downstream)", action):
        return False
    if not re.search(r"timeout|retry|retries|backoff", action):
        return False
    cited = set(option.cited_evidence_ids)
    return not any(
        cited.intersection(obs.evidence_ids)
        and re.search(r"(?:external|downstream|third.party).{0,32}gateway|gateway.{0,32}(?:external|downstream)", f"{obs.claim} {obs.indicator}".lower())
        and re.search(r"timeout|timed out|slow|latency|unavailable|5\d\d|failed|error", f"{obs.claim} {obs.indicator}".lower())
        for obs in context.observations
    )


def _unsupported_mechanism(option: ProposedOption, context: CorrelationContext) -> str | None:
    """Require cited observations for a claimed timeout or transient process state."""
    action = option.action.lower()
    cited = set(option.cited_evidence_ids)
    observed = " ".join(
        f"{obs.claim} {obs.indicator}".lower()
        for obs in context.observations
        if cited.intersection(obs.evidence_ids)
    )
    if re.search(r"\b(?:increase|extend|raise|adjust)\b.{0,50}\btimeout\b", action):
        if not re.search(r"timeout|timed out|latency breach|slow response|slow call", observed):
            return "Cited observations do not establish a timeout or latency problem."
    if "restart" in action and re.search(r"transient state|stale state|reinitialize connections", action):
        if not re.search(r"transient|stale|connection|crash|hung|deadlock", observed):
            return "Cited observations do not establish a transient process or connection state."
    return None


def validate_recommendation_evidence(
    options: list[ProposedOption],
    context: CorrelationContext,
    *,
    resolver: EvidenceResolver | None = None,
) -> list[ValidationRecord]:
    """Validate reference integrity, time/service relevance, and support/contradiction."""
    candidates_by_id = {c.candidate_id: c for c in context.root_cause_candidates}
    f3_links_by_eid, obs_services, cand_contradictions = _index_f3_links(context)
    unresolved_set = set(context.unresolved_evidence_ids)
    records: list[ValidationRecord] = []

    for option in options:
        candidate = candidates_by_id.get(option.candidate_id) if option.candidate_id else None
        cand_link_map = {link.evidence_id: link for link in candidate.evidence_links} if candidate else {}
        checks: list[EvidenceCheckDetail] = []
        links: list[EvidenceLink] = []
        reasons: list[str] = []

        for eid in option.cited_evidence_ids:
            f3_link = cand_link_map.get(eid) or f3_links_by_eid.get(eid)
            has_candidate_link = eid in cand_link_map
            resolved_ref: EvidenceRef | None = resolver.resolve(eid) if resolver is not None else None

            # 1. Reference integrity & availability
            if eid in unresolved_set or (f3_link is not None and f3_link.reference_valid is False):
                reason = f"Evidence '{eid}' failed reference integrity in F3 or is unresolved."
                reasons.append(reason)
                checks.append(
                    EvidenceCheckDetail(
                        evidence_id=eid,
                        reference_valid=False,
                        availability_status="unavailable",
                        relevance_status="unknown",
                        support_status="unverified",
                        relation=f3_link.relation if f3_link else "unknown",
                        reason=reason,
                    )
                )
                links.append(
                    EvidenceLink(
                        evidence_id=eid,
                        relation=f3_link.relation if f3_link else "unknown",
                        reference_valid=False,
                        relevance_status="unknown",
                        support_status="unverified",
                    )
                )
                continue

            if resolver is not None:
                if resolved_ref is None:
                    reason = f"Evidence '{eid}' could not be resolved in the evidence store."
                    reasons.append(reason)
                    checks.append(
                        EvidenceCheckDetail(
                            evidence_id=eid,
                            reference_valid=False,
                            availability_status="unavailable",
                            relevance_status="unknown",
                            support_status="unverified",
                            relation=f3_link.relation if f3_link else "unknown",
                            reason=reason,
                        )
                    )
                    links.append(
                        EvidenceLink(
                            evidence_id=eid,
                            relation=f3_link.relation if f3_link else "unknown",
                            reference_valid=False,
                            relevance_status="unknown",
                            support_status="unverified",
                        )
                    )
                    continue
                if resolved_ref.availability_status in ("expired", "unavailable"):
                    reason = f"Evidence '{eid}' is {resolved_ref.availability_status}."
                    reasons.append(reason)
                    checks.append(
                        EvidenceCheckDetail(
                            evidence_id=eid,
                            reference_valid=True,
                            availability_status=resolved_ref.availability_status,
                            relevance_status="unknown",
                            support_status="unverified",
                            relation=f3_link.relation if f3_link else "unknown",
                            reason=reason,
                        )
                    )
                    links.append(
                        EvidenceLink(
                            evidence_id=eid,
                            relation=f3_link.relation if f3_link else "unknown",
                            reference_valid=True,
                            relevance_status="unknown",
                            support_status="unverified",
                        )
                    )
                    continue
                ref_valid: bool | str = True
                availability = resolved_ref.availability_status
            else:
                if f3_link is None:
                    reason = f"Evidence '{eid}' is not present in F3 correlation evidence links."
                    reasons.append(reason)
                    checks.append(
                        EvidenceCheckDetail(
                            evidence_id=eid,
                            reference_valid=False,
                            availability_status="unavailable",
                            relevance_status="unknown",
                            support_status="unverified",
                            relation="unknown",
                            reason=reason,
                        )
                    )
                    links.append(
                        EvidenceLink(
                            evidence_id=eid,
                            relation="unknown",
                            reference_valid=False,
                            relevance_status="unknown",
                            support_status="unverified",
                        )
                    )
                    continue
                ref_valid = f3_link.reference_valid
                if ref_valid is not True:
                    reason = f"Evidence '{eid}' has reference_valid={ref_valid!r}."
                    reasons.append(reason)
                    checks.append(
                        EvidenceCheckDetail(
                            evidence_id=eid,
                            reference_valid=ref_valid,
                            availability_status="unknown",
                            relevance_status=f3_link.relevance_status,
                            support_status="unverified",
                            relation=f3_link.relation,
                            reason=reason,
                        )
                    )
                    links.append(
                        EvidenceLink(
                            evidence_id=eid,
                            relation=f3_link.relation,
                            reference_valid=ref_valid,
                            relevance_status=f3_link.relevance_status,
                            support_status="unverified",
                        )
                    )
                    continue
                availability = "available"

            # 2. Time & service relevance
            relevant = True
            rel_reasons: list[str] = []
            if f3_link is not None and f3_link.relevance_status == "irrelevant":
                relevant = False
                rel_reasons.append(f"F3 marked '{eid}' as irrelevant.")

            known_services: set[str] = set()
            if resolved_ref is not None:
                known_services.add(resolved_ref.service)
            elif eid in obs_services:
                known_services |= obs_services[eid]

            if known_services and not any(
                _service_matches(svc, option, candidate, context, has_candidate_link)
                for svc in known_services
            ):
                relevant = False
                rel_reasons.append(
                    f"Service mismatch for '{eid}': evidence belongs to {sorted(known_services)} "
                    f"rather than target '{option.target_service or (candidate.service if candidate else 'incident')}'."
                )

            if resolved_ref is not None and context.window is not None:
                if not _windows_overlap(
                    resolved_ref.start_time,
                    resolved_ref.end_time,
                    context.window.start_time,
                    context.window.end_time,
                ):
                    relevant = False
                    rel_reasons.append(
                        f"Time window mismatch for '{eid}': outside incident window "
                        f"[{context.window.start_time.isoformat()}, "
                        f"{context.window.end_time.isoformat() if context.window.end_time else 'open'})."
                    )

            if not relevant:
                reason = " ".join(rel_reasons)
                reasons.append(reason)
                checks.append(
                    EvidenceCheckDetail(
                        evidence_id=eid,
                        reference_valid=True,
                        availability_status=availability,
                        relevance_status="irrelevant",
                        support_status="unverified",
                        relation=f3_link.relation if f3_link else "context_only",
                        reason=reason,
                    )
                )
                links.append(
                    EvidenceLink(
                        evidence_id=eid,
                        relation=f3_link.relation if f3_link else "context_only",
                        reference_valid=True,
                        relevance_status="irrelevant",
                        support_status="unverified",
                    )
                )
                continue

            # 3. Support vs contradiction
            relation = f3_link.relation if f3_link else "context_only"
            if f3_link is not None and (f3_link.relation == "contradicts" or f3_link.support_status == "contradicted"):
                support_status = "contradicted"
                relation = "contradicts"
                reason = f"Evidence '{eid}' contradicts the proposed claim."
                reasons.append(reason)
            elif f3_link is not None and f3_link.relation == "supports" and f3_link.support_status == "supported":
                support_status = "supported"
                reason = f"Evidence '{eid}' is valid, relevant, and supports the option."
            elif resolved_ref is not None and f3_link is None and option.action_type in ("verification", "investigation"):
                support_status = "supported"
                relation = "supports"
                reason = f"Evidence '{eid}' resolves cleanly and justifies follow-up {option.action_type}."
            else:
                support_status = "unverified"
                reason = f"Evidence '{eid}' is resolvable but does not verify causation for this option."
                reasons.append(reason)

            checks.append(
                EvidenceCheckDetail(
                    evidence_id=eid,
                    reference_valid=True,
                    availability_status=availability,
                    relevance_status="relevant",
                    support_status=support_status,
                    relation=relation,
                    reason=reason,
                )
            )
            links.append(
                EvidenceLink(
                    evidence_id=eid,
                    relation=relation,
                    reference_valid=True,
                    relevance_status="relevant",
                    support_status=support_status,
                )
            )

        # Evaluate overall option validation
        supported_checks = [c for c in checks if c.support_status == "supported"]
        contradicted_checks = [c for c in checks if c.support_status == "contradicted"]
        broken_checks = [c for c in checks if c.reference_valid is not True or c.availability_status != "available"]
        irrelevant_checks = [c for c in checks if c.relevance_status == "irrelevant"]
        unverified_checks = [
            c for c in checks if c.support_status == "unverified" and c.reference_valid is True and c.relevance_status == "relevant"
        ]
        cand_contra_links = cand_contradictions.get(option.candidate_id or "", [])

        total = len(checks)
        support_strength = round(len(supported_checks) / total, 4) if total > 0 else 0.0
        validation: ValidationStatus

        if option.action_type == "remediation":
            # A remediation must name the F3 hypothesis it is meant to address,
            # and every cited item must be linked to that candidate. Falling back
            # to the global evidence index can otherwise make unrelated evidence
            # appear to support a free-floating LLM proposal.
            if not option.candidate_id:
                validation = "unsupported"
                reasons.append("Remediation must identify its associated F3 candidate.")
            elif candidate is None:
                validation = "unsupported"
                reasons.append(
                    f"Remediation references unknown F3 candidate '{option.candidate_id}'."
                )
            elif any(eid not in cand_link_map for eid in option.cited_evidence_ids):
                validation = "unsupported"
                reasons.append("Remediation cites evidence not linked to its associated F3 candidate.")
            elif context.status == "inconclusive" or context.leading_candidate_id is None:
                validation = "unsupported"
                reasons.append("F3 diagnosis is inconclusive; remediations cannot be validated without a leading cause.")
            elif _unsupported_external_gateway_fix(option, context):
                validation = "unsupported"
                reasons.append("Cited observations do not establish a failed or slow external gateway call.")
            elif mechanism_reason := _unsupported_mechanism(option, context):
                validation = "unsupported"
                reasons.append(mechanism_reason)
            elif not supported_checks:
                validation = "unsupported"
                if not checks:
                    reasons.append("Remediation cites no current incident evidence.")
            elif contradicted_checks and len(contradicted_checks) >= len(supported_checks):
                validation = "unsupported"
                reasons.append("Contradicting evidence outweighs or negates supporting citations.")
            elif (
                contradicted_checks
                or cand_contra_links
                or broken_checks
                or irrelevant_checks
                or unverified_checks
                or (option.candidate_id is not None and option.candidate_id != context.leading_candidate_id)
                or context.status == "partial"
            ):
                validation = "conditional"
                if cand_contra_links and not contradicted_checks:
                    reasons.append("Associated root-cause candidate has contradicting evidence in F3.")
                if option.candidate_id is not None and option.candidate_id != context.leading_candidate_id:
                    reasons.append("Option targets a secondary/alternative candidate rather than the leading cause.")
                if context.status == "partial":
                    reasons.append("F3 correlation is partial; remaining evidence gaps exist.")
            else:
                validation = "supported"
                reasons.append("All cited evidence is valid, relevant, and supporting for the leading candidate.")
        else:
            # verification or investigation actions remain distinguishable from remediations
            if checks and (broken_checks or irrelevant_checks) and not supported_checks:
                validation = "unsupported"
            elif checks and contradicted_checks and not supported_checks:
                validation = "unsupported"
            elif not checks:
                if context.missing_evidence or context.unresolved_evidence_ids:
                    validation = "supported"
                    support_strength = 0.5
                    reasons.append("Action is directly justified by explicit F3 missing/unresolved evidence gaps.")
                else:
                    validation = "unsupported"
                    reasons.append("Verification/investigation option lacks both evidence links and documented F3 gaps.")
            elif broken_checks or irrelevant_checks or contradicted_checks or cand_contra_links:
                validation = "conditional"
            else:
                validation = "supported"
                reasons.append("Action is supported by valid incident citations or documented gaps.")

        records.append(
            ValidationRecord(
                recommendation_id=option.recommendation_id,
                action_type=option.action_type,
                validation=validation,
                support_strength=support_strength,
                reasons=list(dict.fromkeys(reasons)),
                evidence_links=links,
                checks=checks,
            )
        )
    return records
