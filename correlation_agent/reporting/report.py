"""Build the recommendation handoff from a model explanation and verified F2 rows."""
import re
from html import escape

from pydantic import BaseModel, Field

from investigation.observa_investigation.contracts import InvestigationResult, MissingEvidence
from ..agent.state import Candidate, CorrelationReport, IncidentHistory, Observation, ReportStep


class ProposedEffect(BaseModel):
    follows_step: int = Field(ge=1, description="Earlier explanatory step. The probable origin is step 1.")
    service: str = Field(min_length=1, description="Service where the effect was observed.")
    explanation: str = Field(min_length=1, description="Explain how the earlier step likely produced this symptom.")
    evidence_rows: list[int] = Field(min_length=1, description="Numbered observations supporting this effect on this service.")


def build_report(source: InvestigationResult, rows: list[Observation], origin: Candidate | None,
                 effects: list[ProposedEffect], gaps: list[MissingEvidence],
                 history: IncidentHistory | None = None) -> CorrelationReport:
    steps = []
    if origin is None:
        summary = "The available evidence does not establish a probable origin."
        for i, row in enumerate(rows, 1):
            steps.append(ReportStep(step=i, kind="observation", follows_step=None,
                                    service=row.service, explanation=row.claim, evidence=[row]))
    else:
        evidence = [row for row in rows if row.service == origin.service
                    and row.snapshot_id == origin.snapshot_id and row.finding_id in origin.finding_ids]
        steps.append(ReportStep(step=1, kind="probable_origin", follows_step=None, service=origin.service,
                                explanation=origin.hypothesis, evidence=evidence))
        model_steps = {1: 1}
        for number, effect in enumerate(effects, 2):
            if effect.follows_step >= number:
                raise ValueError("Each effect must refer to an earlier report step; origin is step 1.")
            if any(i < 1 or i > len(rows) for i in effect.evidence_rows):
                raise ValueError("Each report effect must cite existing observation row numbers.")
            evidence = [rows[i - 1] for i in sorted(set(effect.evidence_rows))]
            if any(r.service != effect.service or r.snapshot_id != origin.snapshot_id for r in evidence):
                raise ValueError("Effect evidence must match the affected service and the origin snapshot.")
            parent = model_steps.get(effect.follows_step)
            if parent is None or steps[parent - 1].service != effect.service:
                # With F1 history we derive verified hops below. Standalone F2
                # input can document the symptom, but cannot establish this hop.
                if history is None:
                    gaps.append(MissingEvidence(required=f"verified dependency for {effect.service}",
                                                reason="The finding shows a symptom without a directed call.",
                                                requested_from="traces", status="unavailable"))
                    steps.append(ReportStep(step=len(steps) + 1, kind="observation", follows_step=None,
                                            service=effect.service, explanation=evidence[0].claim, evidence=evidence))
                continue
            model_steps[number] = len(steps) + 1
            steps.append(ReportStep(step=len(steps) + 1, kind="inferred_effect",
                                    follows_step=parent, service=effect.service,
                                    explanation=effect.explanation, evidence=evidence))
        if history:
            by_service = {origin.service: 1}
            # Walk back from the origin along witnessed failed calls. This also
            # recovers intermediate callers for which F2 emitted no top finding.
            for service in _upstream_services(origin.service, history):
                parent = by_service[service.callee]
                claim = f"{service.caller} recorded a failed request to {service.callee}."
                row = Observation(finding_id=service.evidence_id, signal=service.signal, service=service.caller,
                                  claim=claim, snapshot_id=service.evidence_id.split("/")[1],
                                  evidence_ids=[ref for ref in (service.parent_evidence_id, service.evidence_id) if ref])
                steps.append(ReportStep(step=len(steps) + 1, kind="inferred_effect", follows_step=parent,
                                        service=service.caller, explanation=claim + " The failure likely affected the caller.",
                                        evidence=[row]))
                by_service[service.caller] = len(steps)
            for svc in sorted(set(history.affected_services) - by_service.keys()):
                gaps.append(MissingEvidence(required=f"verified failure path to {svc}",
                            reason=f"No directed failed call links this service to the probable origin {origin.service}.",
                            requested_from="traces", status="unavailable"))
        summary = " ".join([f"Probable origin in {origin.service}: {origin.hypothesis}"] +
                           [s.explanation for s in steps[1:]])
    return CorrelationReport(incident_id=source.incident_id, source_run_id=source.run_id,
                             status="probable_cause" if origin else "needs_more_analysis",
                             summary=summary, steps=steps, missing_evidence=gaps, history=history)


def _upstream_services(origin, history):
    callers = [origin]
    seen = {origin}
    for callee in callers:
        for edge in history.dependencies:
            if edge.callee == callee and edge.caller not in seen:
                seen.add(edge.caller)
                callers.append(edge.caller)
                yield edge


def render_markdown(report: CorrelationReport) -> str:
    """Render the validated report and its citations without another model call."""
    def plain(value: str) -> str:
        text = escape(" ".join(value.splitlines()), quote=False)
        return re.sub(r"([\\`*_\[\]~|])", r"\\\1", text)

    def code(value: str) -> str:
        fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", value)), default=0))
        return f"{fence} {' '.join(value.splitlines())} {fence}"

    lines = [f"# Correlation report · {plain(report.incident_id)}", "",
             f"**Run** {code(report.source_run_id)}", "",
             f"**Status** {report.status.replace('_', ' ').capitalize()}", "",
             "## Summary", "", plain(report.summary)]
    if report.history:
        h = report.history
        lines.extend(["", f"## Incident history · {h.phase}", "",
                      f"- First observed: {plain(h.first_observed_at)} · {code(h.first_snapshot)}",
                      f"- {'Resolved at: ' + plain(h.resolved_at) if h.resolved_at else 'Open at snapshot'} · {code(h.snapshot_id)}",
                      "", "### Highest recorded alert values", ""])
        for peak in h.peaks:
            lines.append(f"- {plain(peak.service)} · {code(peak.rule)} · {peak.value:g} {plain(peak.unit)}"
                         f" · recorded by {plain(peak.recorded_at)} · {code(peak.evidence_id)}")
        if h.release_findings:
            lines.extend(["", "### Release and error timing", ""])
            for row in h.release_findings:
                lines.append(f"- {plain(row.claim)} " + ", ".join(code(ref) for ref in row.evidence_ids))
    for step in report.steps:
        kind = step.kind.replace("_", " ").capitalize()
        lines.extend(["", f"## {step.step}. {kind} · {plain(step.service)}", ""])
        if step.follows_step is not None:
            lines.extend([f"Likely consequence of step {step.follows_step}.", ""])
        lines.extend([plain(step.explanation), "", "**Evidence**", ""])
        for row in step.evidence:
            refs = ", ".join(code(ref) for ref in row.evidence_ids)
            lines.append(f"- **{row.signal}** · {plain(row.claim)}")
            lines.append(f"  Finding {code(row.finding_id)} · References {refs}")
    if report.missing_evidence:
        lines.extend(["", "## Missing evidence", ""])
        for gap in report.missing_evidence:
            lines.append(f"- **{gap.requested_from}** · {plain(gap.required)} — {plain(gap.reason)}")
    return "\n".join(lines) + "\n"
