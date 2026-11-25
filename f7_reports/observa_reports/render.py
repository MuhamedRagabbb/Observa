"""Markdown export of one report version (F7-US-02).

Presentation only: it renders what the content says, labels observations,
inferences, contradictions and unresolved references, and never strengthens a
claim. Upstream text is passed through as data.
"""
from datetime import datetime

from .content import CauseItem, ReportContent
from .service import VersionRecord

SIGNALS = ("logs", "metrics", "traces")
REFERENCE_LABEL = {"valid": "Resolved", "invalid": "⚠ Invalid reference", "unresolved": "⚠ Unresolved reference"}


def _ts(value: datetime | None) -> str:
    return value.isoformat().replace("+00:00", "Z") if value else "not recorded"


def _links(links) -> str:
    return "; ".join(f"`{l.evidence_id}` {l.relation}/{l.support_status}" for l in links) or "none"


def _cause(cause: CauseItem, label: str) -> list[str]:
    lines = [f"**{label}** · `{cause.candidate_id}`", "", cause.description, ""]
    lines.append(f"- Uncertainty: {cause.uncertainty or 'none stated'}")
    lines += [f"- Evidence `{l.evidence_id}`: {l.relation} / {l.support_status} (reference "
              f"{'resolved' if l.reference_valid is True else 'unresolved' if l.reference_valid == 'unknown' else 'invalid'})"
              for l in cause.evidence_links]
    return lines + [""]


def render_markdown(content: ReportContent, record: VersionRecord) -> str:
    o = content.overview
    out = [f"# Incident report · {o.incident_id}", "",
           f"Version {record.version} · draft, pending human review · `{record.report_id}` · "
           f"content `{record.content_ref}`", "", f"> {content.draft_notice}", "",
           "## Overview", "",
           f"- Title: {o.title}",
           f"- Start: `{_ts(o.start_time)}` · End: `{_ts(o.end_time)}`",
           f"- Affected services: {', '.join(o.affected_services) or 'none recorded'}",
           f"- Severity: {o.severity or 'not provided'}", "",
           "## Executive summary", "", content.executive_summary, "",
           "## Grouped alerts", ""]
    out += [f"- **{a.signal_type}** · {a.service} · {a.severity} · `{_ts(a.timestamp)}` · {a.description}"
            for a in content.alerts.items] or ["- No alert records were supplied."]
    out += [f"- `{alert_id}`: alert record not supplied" for alert_id in content.alerts.missing_alert_ids]
    out += ["", "## Timeline", ""]
    out += [f"- `{_ts(e.at)}` {e.description}" for e in content.timeline.events]
    out += [f"- (time not recorded) {e.description} · `{e.ref}`" for e in content.timeline.untimed]
    out += ["", "## Findings by signal", ""]
    for signal in SIGNALS:
        section = getattr(content.findings, signal)
        out += [f"### **{signal}** · status {section.status}", ""]
        if not section.findings:
            out += ["No findings were supplied." if section.status == "unavailable" else "No findings.", ""]
        for f in section.findings:
            kind = "Observed" if f.claim_kind == "observation" else "Inferred"
            out.append(f"- {kind}: {f.claim} · {f.service} · `{f.finding_id}`")
            if f.uncertainty:
                out.append(f"  - Uncertainty: {f.uncertainty}")
            out.append(f"  - Evidence: {_links(f.evidence_links)}")
        out.append("")
    rc = content.root_cause
    out += ["## Root-cause assessment", ""]
    if rc.status == "leading":
        out += _cause(rc.leading, "Probable cause (not verified)")
    elif rc.status == "inconclusive":
        out += ["No leading cause: the correlation is inconclusive.", ""]
    else:
        out += ["No root-cause assessment is available.", ""]
    for alternative in rc.alternatives:
        out += _cause(alternative, "Alternative hypothesis")
    out += ["## Recommendations", ""]
    recs = content.recommendations
    if recs.status == "unavailable":
        out += ["Recommendations are unavailable.", ""]
    elif recs.status == "no_validated_remediation":
        out += ["No validated remediation is proposed.", ""]
    for item in recs.ranked + recs.other_options:
        rank = f"{item.rank}." if item.rank else "-"
        out.append(f"{rank} {item.action} · {item.action_type} · validation {item.validation}")
        out.append(f"   - Rationale: {item.rationale}")
        if item.risk_or_caveat:
            out.append(f"   - Caveat: {item.risk_or_caveat}")
    out += ["", "## Evidence register", "", "| Evidence | Signal | Services | Reference | Assessments |",
            "| --- | --- | --- | --- | --- |"]
    for e in content.evidence_register:
        assessments = "; ".join(f"{a.claim_type} {a.relation}/{a.support_status}" for a in e.assessments)
        out.append(f"| `{e.evidence_id}` | {e.signal_type or 'unknown'} | {', '.join(e.services) or '-'} | "
                   f"{REFERENCE_LABEL[e.reference_status]} | {assessments} |")
    out += ["", "## Missing evidence", ""]
    out += [f"- [{m.source}] {m.required} ({m.requested_from}, {m.status}): {m.reason}"
            for m in content.missing_evidence] or ["- None reported."]
    out += ["", "## Limitations", ""]
    out += [f"- {line}" for line in content.limitations] or ["- None reported."]
    return "\n".join(out) + "\n"
