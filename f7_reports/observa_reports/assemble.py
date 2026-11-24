"""Assemble the structured incident report from F1-F4 outputs (F7-US-01).

assemble() is a pure function: no clock, no I/O, no generated IDs. It maps what
upstream supplied and marks what is missing; it never strengthens a claim,
invents a timestamp or fills a gap with a guess.
"""
from observa_investigation.contracts import EvidenceLink

from .content import (
    AlertItem, AlertsSection, Assessment, CauseItem, EvidenceEntry, FindingItem, Findings, MissingItem,
    Overview, RecommendationItem, Recommendations, ReportContent, RootCause, SignalSection, Timeline,
    TimelineEvent,
)
from .inputs import CorrelationResultIn, RecommendationResultIn, ReportInputs

SIGNALS = ("logs", "metrics", "traces")


def assemble(inputs: ReportInputs) -> ReportContent:
    findings = _findings(inputs)
    root_cause = _root_cause(inputs.correlation)
    recommendations = _recommendations(inputs.recommendations)
    return ReportContent(
        overview=Overview(
            incident_id=inputs.incident.incident_id, title=inputs.incident.title,
            start_time=inputs.incident.start_time, end_time=inputs.incident.end_time,
            affected_services=list(inputs.incident.affected_services), severity=inputs.incident.severity),
        executive_summary=_summary(inputs, root_cause, recommendations),
        alerts=_alerts(inputs),
        timeline=_timeline(inputs),
        findings=findings,
        root_cause=root_cause,
        evidence_register=_evidence_register(findings, root_cause, recommendations),
        missing_evidence=_missing(inputs),
        recommendations=recommendations,
        limitations=_limitations(inputs),
    )


def _alerts(inputs: ReportInputs) -> AlertsSection:
    records = {a.alert_id: a for a in inputs.alerts}
    items = [AlertItem(alert_id=a.alert_id, signal_type=a.signal_type, service=a.service, severity=a.severity,
                       timestamp=a.timestamp, description=a.description, evidence_ids=list(a.evidence_ids))
             for a in sorted(inputs.alerts, key=lambda a: (a.timestamp, a.alert_id))]
    return AlertsSection(items=items, missing_alert_ids=[i for i in inputs.incident.alerts if i not in records])


def _timeline(inputs: ReportInputs) -> Timeline:
    incident = inputs.incident
    events = [TimelineEvent(at=incident.start_time, kind="incident_started", ref=incident.incident_id,
                            description="Incident first observed by F1")]
    for a in inputs.alerts:
        events.append(TimelineEvent(at=a.timestamp, kind="alert_raised", ref=a.alert_id,
                                    description=f"{a.service}: {a.description}"))
        if a.last_seen is not None:
            events.append(TimelineEvent(at=a.last_seen, kind="alert_last_seen", ref=a.alert_id,
                                        description=f"{a.service}: alert last observed"))
    if incident.end_time is not None:
        events.append(TimelineEvent(at=incident.end_time, kind="incident_ended", ref=incident.incident_id,
                                    description="Incident end recorded by F1"))
    order = {"incident_started": 0, "alert_raised": 1, "alert_last_seen": 2, "incident_ended": 3}
    events.sort(key=lambda e: (e.at, order[e.kind], e.ref))
    recorded = {a.alert_id for a in inputs.alerts}
    untimed = [TimelineEvent(at=None, kind="alert_unrecorded", ref=alert_id,
                             description="Alert grouped by F1; no alert record or time was supplied")
               for alert_id in incident.alerts if alert_id not in recorded]
    return Timeline(events=events, untimed=untimed)


def _findings(inputs: ReportInputs) -> Findings:
    sections = {}
    for signal in SIGNALS:
        if inputs.investigation is None:
            sections[signal] = SignalSection(status="unavailable", findings=[])
            continue
        output = getattr(inputs.investigation, f"{signal}_output")
        sections[signal] = SignalSection(status=output.status, findings=[
            FindingItem(finding_id=f.finding_id, service=f.service, claim=f.claim, claim_kind=f.claim_kind,
                        indicator=f.indicator, execution_status=f.execution_status, uncertainty=f.uncertainty,
                        evidence_links=list(f.evidence_links))
            for f in output.findings])
    return Findings(**sections)


def _cause(candidate) -> CauseItem:
    return CauseItem(candidate_id=candidate.candidate_id, description=candidate.description,
                     uncertainty=candidate.uncertainty, evidence_links=list(candidate.evidence_links))


def _root_cause(correlation: CorrelationResultIn | None) -> RootCause:
    if correlation is None:
        return RootCause(status="unavailable", correlation_status=None, correlation_run_id=None,
                         leading=None, alternatives=[])
    leading = next((c for c in correlation.root_cause_candidates
                    if c.candidate_id == correlation.leading_candidate_id), None)
    status = ("unavailable" if correlation.status == "failed"
              else "leading" if leading is not None else "inconclusive")
    return RootCause(status=status, correlation_status=correlation.status, correlation_run_id=correlation.run_id,
                     leading=_cause(leading) if leading else None,
                     alternatives=[_cause(c) for c in correlation.root_cause_candidates if c is not leading])


def _recommendation(option, rank) -> RecommendationItem:
    return RecommendationItem(rank=rank, recommendation_id=option.recommendation_id, action=option.action,
                              action_type=option.action_type, validation=option.validation,
                              rationale=option.rationale, risk_or_caveat=option.risk_or_caveat,
                              evidence_links=list(option.evidence_links))


def _recommendations(result: RecommendationResultIn | None) -> Recommendations:
    if result is None:
        return Recommendations(status="unavailable", recommendation_status=None, ranked=[], other_options=[],
                               caveats=[])
    by_id = {o.recommendation_id: o for o in result.recommendation_options}
    ranked = [_recommendation(by_id[rid], n) for n, rid in enumerate(result.ranked_recommendation_ids, 1)]
    others = [_recommendation(o, None) for o in result.recommendation_options
              if o.recommendation_id not in set(result.ranked_recommendation_ids)]
    caveats = [o.risk_or_caveat for o in result.recommendation_options if o.risk_or_caveat]
    return Recommendations(status="ranked" if ranked else "no_validated_remediation",
                           recommendation_status=result.status, ranked=ranked, other_options=others,
                           caveats=caveats)


def _reference_status(link: EvidenceLink) -> str:
    return {True: "valid", False: "invalid"}.get(link.reference_valid, "unresolved")


def _parse(evidence_id: str):
    """Best-effort read of F2's '{incident}/{snapshot}/{signal}#{row}' form; other formats stay opaque."""
    parts = evidence_id.split("/")
    if len(parts) == 3 and "#" in parts[2]:
        signal = parts[2].split("#", 1)[0]
        return (signal if signal in SIGNALS else None), parts[1] or None
    return None, None


def _evidence_register(findings: Findings, root_cause: RootCause, recs: Recommendations) -> list[EvidenceEntry]:
    entries: dict[str, dict] = {}

    def add(link: EvidenceLink, claim_type: str, claim_ref: str, service: str | None):
        entry = entries.setdefault(link.evidence_id, {"links": [], "services": set(), "assessments": []})
        entry["links"].append(link)
        if service:
            entry["services"].add(service)
        entry["assessments"].append(Assessment(claim_type=claim_type, claim_ref=claim_ref, relation=link.relation,
                                               support_status=link.support_status,
                                               relevance_status=link.relevance_status))

    for signal in SIGNALS:
        for f in getattr(findings, signal).findings:
            for link in f.evidence_links:
                add(link, "finding", f.finding_id, f.service)
    for cause in ([root_cause.leading] if root_cause.leading else []) + root_cause.alternatives:
        for link in cause.evidence_links:
            add(link, "root_cause_candidate", cause.candidate_id, None)
    for item in recs.ranked + recs.other_options:
        for link in item.evidence_links:
            add(link, "recommendation", item.recommendation_id, None)

    register = []
    for evidence_id in sorted(entries):
        entry = entries[evidence_id]
        statuses = {_reference_status(link) for link in entry["links"]}
        # The weakest assessment wins: one unresolved citation keeps the reference flagged.
        status = "invalid" if "invalid" in statuses else "unresolved" if "unresolved" in statuses else "valid"
        signal, snapshot = _parse(evidence_id)
        register.append(EvidenceEntry(evidence_id=evidence_id, signal_type=signal, snapshot_id=snapshot,
                                      services=sorted(entry["services"]), reference_status=status,
                                      assessments=entry["assessments"]))
    return register


def _missing(inputs: ReportInputs) -> list[MissingItem]:
    items, seen = [], set()
    sources = [("F2", inputs.investigation.missing_evidence if inputs.investigation else []),
               ("F3", inputs.correlation.missing_evidence if inputs.correlation else [])]
    for source, gaps in sources:
        for gap in gaps:
            key = (source, gap.required, gap.requested_from, gap.reason)
            if key not in seen:
                seen.add(key)
                items.append(MissingItem(source=source, required=gap.required, reason=gap.reason,
                                         requested_from=gap.requested_from, status=gap.status))
    return items


def _limitations(inputs: ReportInputs) -> list[str]:
    notes = []
    if inputs.investigation is None:
        notes.append("Signal findings were not provided by F2.")
    elif inputs.investigation.status != "completed":
        notes.append(f"F2 investigation status is {inputs.investigation.status}; some signal results are incomplete.")
    if inputs.correlation is None:
        notes.append("Root-cause correlation was not provided by F3.")
    elif inputs.correlation.status == "failed":
        error = inputs.correlation.error
        notes.append(f"F3 correlation failed ({error.code}): {error.message}")
    if inputs.recommendations is None:
        notes.append("Recommendations were not provided by F4.")
    elif inputs.recommendations.status == "failed":
        notes.append("F4 recommendation generation failed.")
    missing_alerts = [i for i in inputs.incident.alerts if i not in {a.alert_id for a in inputs.alerts}]
    if missing_alerts:
        notes.append(f"{len(missing_alerts)} grouped alert(s) have no alert record: {', '.join(missing_alerts)}.")
    return notes


def _summary(inputs: ReportInputs, root_cause: RootCause, recs: Recommendations) -> str:
    services = ", ".join(inputs.incident.affected_services) or "no services recorded"
    window = "is ongoing" if inputs.incident.end_time is None else "has ended"
    parts = [f"{inputs.incident.title}. The incident {window} and affects {services}."]
    if root_cause.status == "leading":
        parts.append(f"Probable cause (not verified by a human): {root_cause.leading.description}")
    elif root_cause.status == "inconclusive":
        parts.append("Correlation found no leading cause; the evidence is inconclusive.")
    else:
        parts.append("No root-cause assessment is available.")
    if recs.status == "ranked":
        parts.append(f"{len(recs.ranked)} ranked recommendation(s); top: {recs.ranked[0].action}.")
    elif recs.status == "no_validated_remediation":
        parts.append("No validated remediation is proposed.")
    else:
        parts.append("Recommendations are unavailable.")
    return " ".join(parts)
