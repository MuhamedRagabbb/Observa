"""Evidence processing for F3. Only current F2 observations can support a cause."""
from collections import defaultdict
from collections.abc import Callable
from uuid import uuid4
from pydantic import BaseModel, Field
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from investigation.observa_investigation.contracts import (
    SIGNALS, FollowUpRequest, InvestigationResult, MissingEvidence, SignalType,
)
from .state import Candidate, CorrelationResult, Observation
from ..reporting.report import ProposedEffect, build_report

MemoryLookup = Callable[[str, list[str]], list[str]]


def get_structured_file(source: InvestigationResult | dict) -> InvestigationResult:
    """Accept either the orchestrator's model or its JSON representation."""
    if hasattr(source, "model_dump"):
        source = source.model_dump()
    return InvestigationResult.model_validate(source)


def check_memory(report: InvestigationResult, lookup: MemoryLookup | None) -> list[str]:
    if lookup is None:
        return []
    services = {f.service for s in SIGNALS for f in getattr(report, f"{s}_output").findings}
    return lookup(report.incident_id, sorted(services))


def collect_supported_observations(report: InvestigationResult) -> list[Observation]:
    """Select citable observations; the model interprets the links between them."""
    observations = []
    for signal in SIGNALS:
        output = getattr(report, f"{signal}_output")
        if output.status not in ("partial", "completed"):
            continue
        for finding in output.findings:
            if (finding.signal_type != signal or finding.claim_kind != "observation"
                    or finding.execution_status not in ("partial", "completed")):
                continue
            by_snapshot = defaultdict(set)
            disputed = set()
            for link in finding.evidence_links:
                if link.reference_valid is not True or link.relevance_status != "relevant":
                    continue
                parts = link.evidence_id.split("/", 2)
                if len(parts) != 3 or parts[0] != report.incident_id or not parts[1]:
                    continue
                ref, sep, row = parts[2].partition("#")
                if ref != signal or not sep or not row:
                    continue
                if link.support_status == "contradicted" or link.relation == "contradicts":
                    disputed.add(parts[1])
                elif link.relation == "supports" and link.support_status == "supported":
                    by_snapshot[parts[1]].add(link.evidence_id)
            for snapshot, refs in by_snapshot.items():
                if snapshot not in disputed:
                    observations.append(Observation(
                        finding_id=finding.finding_id, signal=signal, service=finding.service,
                        claim=finding.claim, snapshot_id=snapshot, evidence_ids=sorted(refs),
                    ))
    return observations


@tool("InspectFinding")
def inspect_finding(finding_id: str, runtime: ToolRuntime) -> list[dict]:
    """Read the original F2 finding, including its uncertainty and evidence links."""
    report = runtime.state["investigation"]
    return [f.model_dump() for signal in SIGNALS for f in getattr(report, f"{signal}_output").findings
            if f.finding_id == finding_id]


class Hypothesis(BaseModel):
    service: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    supporting_rows: list[int] = Field(min_length=1, description="Direct observation row numbers for this service")
    related_rows: list[int] = Field(description="Other contextual row numbers in the same snapshot, or []")


class SubmitCause(BaseModel):
    """Finalize a unique probable cause supported by two independent signals. Rank strongest first."""
    candidates: list[Hypothesis] = Field(min_length=1)
    effects: list[ProposedEffect]


class EvidenceRequest(BaseModel):
    signal: SignalType
    required: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RequestEvidence(BaseModel):
    """Ask F2 for specific missing evidence. Use when the leading cause is uncertain."""
    requests: list[EvidenceRequest] = Field(min_length=1)
    candidates: list[Hypothesis] = Field(description="Tentative grounded candidates in ranked order, or []")


def _candidate(proposal: Hypothesis, rows: list[Observation]) -> Candidate:
    indices = set(proposal.supporting_rows + proposal.related_rows)
    if any(i < 1 or i > len(rows) for i in indices):
        raise ValueError("Cite only row numbers in the observations table.")
    support = [rows[i - 1] for i in sorted(set(proposal.supporting_rows))]
    snapshot = support[0].snapshot_id
    if proposal.service.casefold() == "unknown" or any(r.service != proposal.service for r in support):
        raise ValueError("Direct support must belong to the named service. Put propagation in related_rows.")
    if any(rows[i - 1].snapshot_id != snapshot for i in indices):
        raise ValueError("Each candidate must cite a single snapshot.")
    return Candidate(service=proposal.service, hypothesis=proposal.explanation,
                     snapshot_id=snapshot, signals=sorted({r.signal for r in support}),
                     finding_ids=sorted({r.finding_id for r in support}),
                     evidence_ids=sorted({e for r in support for e in r.evidence_ids}),
                     related_finding_ids=sorted({rows[i-1].finding_id for i in
                                                 set(proposal.related_rows) - set(proposal.supporting_rows)}),
                     origin_in_trace=any(r.signal == "traces" and "(deepest failure origin)" in r.claim
                                         for r in support))


def resolve(decision: SubmitCause | RequestEvidence, report: InvestigationResult,
            rows: list[Observation], memory: list[str]) -> CorrelationResult:
    candidates = [_candidate(p, rows) for p in decision.candidates]
    gaps = list(report.missing_evidence)
    follow_up = None
    final = None
    if isinstance(decision, SubmitCause):
        final = candidates[0]
        if len(final.signals) < 2:
            raise ValueError("Finalizing needs two distinct signal types for the proposed cause. Request evidence if insufficient.")
    else:
        for req in decision.requests:
            if not any(g.required == req.required and g.requested_from == req.signal for g in gaps):
                gaps.append(MissingEvidence(required=req.required, reason=req.reason,
                                            requested_from=req.signal, status="unavailable"))
        follow_up = FollowUpRequest(request_id=f"REQ-{uuid4().hex}", incident_id=report.incident_id,
                                    origin="correlation", source_run_id=report.run_id,
                                    target_agents=sorted({r.signal for r in decision.requests}),
                                    objectives=[f"{r.required}: {r.reason}" for r in decision.requests],
                                    evidence_needed=[r.required for r in decision.requests])
    handoff = build_report(report, rows, final, decision.effects if final else [], gaps)
    return CorrelationResult(report=handoff, incident_id=report.incident_id, source_run_id=report.run_id,
                             agent_status="completed" if final else "needs_more_analysis",
                             correlated_findings=rows, relevant_data_from_memory=memory,
                             root_cause_candidates=candidates, final_root_cause=final,
                             missing_evidence=gaps, follow_up_request=follow_up)


def _record_decision(decision: SubmitCause | RequestEvidence, runtime: ToolRuntime) -> Command | str:
    state = runtime.state
    try:
        result = resolve(decision, state["investigation"], state["observations"], state["memory"])
    except ValueError as exc:
        return str(exc)  # The agent can correct its citations or request more evidence.
    receipt = ToolMessage(content=result.agent_status, tool_call_id=runtime.tool_call_id)
    return Command(update={"result": result, "messages": [receipt]})


@tool("SubmitCause")
def submit_cause(candidates: list[Hypothesis], effects: list[ProposedEffect], runtime: ToolRuntime) -> Command | str:
    """Submit ranked causes and ordered probable effects of the leading cause. Use [] for effects if unsupported."""
    if not candidates:
        return "Supply at least one candidate or call RequestEvidence."
    return _record_decision(SubmitCause(candidates=candidates, effects=effects), runtime)


@tool("RequestEvidence")
def request_evidence(requests: list[EvidenceRequest], candidates: list[Hypothesis],
                     runtime: ToolRuntime) -> Command | str:
    """Request specific evidence from F2. Include ranked tentative candidates, or an empty list."""
    if not requests:
        return "Specify which signal and evidence F2 should collect."
    return _record_decision(RequestEvidence(requests=requests, candidates=candidates), runtime)


