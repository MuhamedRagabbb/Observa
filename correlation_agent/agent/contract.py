"""Adapter from F3 internal state to the proposed shared contract v1."""
import hashlib

from .state import (Candidate, ContractCandidate, ContractCorrelationResult, ContractErrorEnvelope,
                    ContractEvidenceLink, CorrelationResult, Observation)


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode()).hexdigest()[:16]}"


def _candidate_links(candidate: Candidate, rows: list[Observation]) -> list[ContractEvidenceLink]:
    """Publish what the candidate cited, from its own snapshot only.

    F2 reuses finding IDs across snapshots, so rows are matched on finding ID and snapshot.
    Cited evidence is support; related rows are context; contradictions stay visible.
    """
    links = [ContractEvidenceLink(evidence_id=evidence_id, relation="supports", reference_valid=True,
                                  relevance_status="relevant", support_status="supported")
             for evidence_id in candidate.evidence_ids]
    cited = set(candidate.finding_ids)
    related = set(candidate.related_finding_ids)
    for row in rows:
        if row.snapshot_id != candidate.snapshot_id or row.finding_id not in cited | related:
            continue
        if row.relation == "contradicts":
            relation, support_status = "contradicts", "contradicted"
        elif row.finding_id in related:
            relation, support_status = "context_only", "unverified"
        else:
            continue  # Supporting rows of cited findings are already covered by evidence_ids.
        links += [ContractEvidenceLink(evidence_id=evidence_id, relation=relation, reference_valid=True,
                                       relevance_status="relevant", support_status=support_status)
                  for evidence_id in row.evidence_ids]
    return links


def to_contract_result(result: CorrelationResult) -> ContractCorrelationResult:
    report = result.report
    history = report.history
    identity = "|".join((result.incident_id, result.source_run_id,
                         history.phase if history else "unknown",
                         history.snapshot_id if history else "unknown"))
    candidates = []
    for candidate in result.root_cause_candidates:
        related_ids = set(candidate.finding_ids) | set(candidate.related_finding_ids)
        links = _candidate_links(candidate, result.correlated_findings)
        links = list({(link.evidence_id, link.relation, link.support_status): link
                      for link in links}.values())
        for item in result.unresolved_evidence:
            if item.finding_id not in related_ids:
                continue
            links.append(ContractEvidenceLink(
                evidence_id=item.evidence_id, relation=item.relation,
                reference_valid="unknown", relevance_status="unknown",
                support_status="unverified"))
        links = list({(link.evidence_id, link.relation, link.support_status): link
                      for link in links}.values())
        candidate_id = _stable_id("CAUSE", "|".join(
            (result.incident_id, candidate.service, *sorted(candidate.evidence_ids))))
        candidates.append(ContractCandidate(candidate_id=candidate_id,
            description=f"{candidate.service}: {candidate.hypothesis}", evidence_links=links,
            uncertainty=None if result.final_root_cause == candidate else
                "Candidate is ranked below the leading cause."))
    leading = None
    if result.final_root_cause is not None:
        leading = _stable_id("CAUSE", "|".join((result.incident_id,
            result.final_root_cause.service, *sorted(result.final_root_cause.evidence_ids))))
    if result.agent_status == "completed" and leading:
        status = "completed"
    elif report.follow_up_stop_reason is not None:
        status = "inconclusive"
    elif result.follow_up_request is not None:
        status = "partial"
    else:
        status = "inconclusive"
    follow_ups = [result.follow_up_request] if result.follow_up_request else []
    run_id = _stable_id("RUN", identity)
    return ContractCorrelationResult(incident_id=result.incident_id, run_id=run_id,
        root_cause_candidates=candidates, leading_candidate_id=leading,
        missing_evidence=result.missing_evidence, status=status, follow_up_requests=follow_ups)


def failed_contract_result(incident_id: str, phase: str, snapshot_id: str, message: str,
                           *, retryable: bool, error_type: str, status_code: int | None = None
                           ) -> ContractCorrelationResult:
    run_id = _stable_id("RUN", "|".join((incident_id, phase, snapshot_id, "failed")))
    return ContractCorrelationResult(incident_id=incident_id, run_id=run_id,
        root_cause_candidates=[], leading_candidate_id=None, missing_evidence=[], status="failed",
        follow_up_requests=[], error=ContractErrorEnvelope(code="INVESTIGATION_FAILED",
            message=" ".join(message.split())[:600], retryable=retryable, operation_id=run_id,
            details={"error_type": error_type, "status_code": status_code}))


def failed_contract_markdown(result: ContractCorrelationResult) -> str:
    error = result.error
    return (f"# Correlation report · {result.incident_id}\n\n"
            f"**Run** `{result.run_id}`\n\n**Status** Failed\n\n"
            f"## Error\n\n**{error.code}** · {error.message}\n")
