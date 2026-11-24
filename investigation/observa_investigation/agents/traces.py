"""Plug the finished Traces Agent (us03_traces_agent / package `traces_agent`) into the orchestrator.

finding_to_agent_result() is a pure mapping, so it is testable without the Traces Agent installed.
"""
from typing import Callable, Optional, Tuple

from ..contracts import EvidenceLink, MissingEvidence, SignalFinding
from .base import AgentResult, AgentTask
from .follow_up import answer_questions


def evidence_id(incident_id: str, snapshot_id: str, row_id: str) -> str:
    """Stable opaque id: the F1 'traces' ref of this snapshot plus the profile row (T1, T2...)."""
    return f"{incident_id}/{snapshot_id}/traces#{row_id}"


def _link(iid, sid, row_id, support) -> EvidenceLink:
    return EvidenceLink(
        evidence_id=evidence_id(iid, sid, row_id),
        relation="supports",
        reference_valid=True,
        relevance_status="relevant",
        support_status=support,
    )


def _describe(row: dict) -> str:
    """Deterministic sentence built only from the profile row: no model text, so it is a true observation."""
    svc = row.get("service") or "unknown"
    op = row.get("operation") or "unknown"
    typ = row.get("type") or "span"
    st = f", HTTP {row['status']}" if row.get("status") is not None else ""
    p95 = f", p95 {row['p95_duration_ms']:g} ms" if row.get("p95_duration_ms") is not None else ""
    origin = " (deepest failure origin)" if row.get("is_deepest_origin") else ""
    return f"{svc} observed {row['count']} '{typ}' events for '{op}'{st}{p95}{origin} in the retained traces"


def finding_to_agent_result(finding: dict, profile: dict) -> AgentResult:
    iid, sid, status = finding["incident_id"], finding["snapshot_id"], finding["status"]
    gaps = profile.get("read_gaps", [])
    records = profile.get("sample", {}).get("traces_count", 0)

    if status == "no_evidence" and gaps and records == 0:
        return AgentResult(status="failed", error="; ".join(gaps))

    missing = [
        MissingEvidence(
            required=f"traces from {svc}",
            requested_from="traces",
            status="unavailable",
            reason=f"no traces or spans found for {svc}",
        )
        for svc in profile.get("services_missing", [])
    ]
    missing += [
        MissingEvidence(
            required="complete traces sample",
            reason=g,
            requested_from="traces",
            status="unavailable",
        )
        for g in gaps
    ]

    if status == "no_evidence":
        missing.insert(
            0,
            MissingEvidence(
                required="trace observations for the incident",
                requested_from="traces",
                reason="no trace evidence in this snapshot",
                status="unavailable",
            ),
        )
        return AgentResult(status="inconclusive", missing_evidence=missing)

    exec_status = "partial" if (missing or status != "ok") else "completed"
    notes = list(finding.get("limits", []))
    if status != "ok":
        notes.append(f"model interpretation not available ({status}); deterministic observations only")
    sample_note = "; ".join(notes) or None

    rows = {r["id"]: r for r in finding.get("evidence", [])}
    findings = [
        SignalFinding(
            finding_id=f"{iid}:traces:F{n}",
            signal_type="traces",
            service=row["service"],
            indicator=row.get("operation"), claim=_describe(row),
            claim_kind="observation",
            evidence_links=[_link(iid, sid, rid, "supported")],
            execution_status=exec_status,
            uncertainty=sample_note,
        )
        for n, (rid, row) in enumerate(rows.items(), 1)
    ]
    if status == "ok" and finding.get("suspected_service"):
        unc = f"confidence: {finding['confidence']}"
        if finding.get("caveats"):
            unc += "; " + " ".join(finding["caveats"])
        findings.append(
            SignalFinding(
                finding_id=f"{iid}:traces:F{len(findings) + 1}",
                signal_type="traces",
                service=finding["suspected_service"],
                claim=finding["hypothesis"],
                claim_kind="inference",
                evidence_links=[_link(iid, sid, rid, "unverified") for rid in rows],
                execution_status=exec_status,
                uncertainty=unc,
            )
        )
    return AgentResult(status=exec_status, findings=findings, missing_evidence=missing)


class TracesSignalAgent:
    signal = "traces"

    def __init__(
        self,
        reports_dir,
        llm=None,
        use_llm: bool = True,
        fetcher_factory: Optional[Callable[[str, str], Tuple[Callable, Callable]]] = None,
    ):
        self.reports_dir, self.llm, self.use_llm = reports_dir, llm, use_llm
        self.fetcher_factory = fetcher_factory

    def run(self, task: AgentTask) -> AgentResult:
        from traces_agent.finding import build_finding
        from traces_agent.llm import LLMClient, LLMError
        from traces_agent.profiler import profile_traces
        from traces_agent.reader import make_fetchers, read_all_traces

        incident = task.incident
        iid = incident.get("incident_id")
        if not task.snapshot_id:
            return AgentResult(status="failed", error="incident has no snapshot_id")
        factory = self.fetcher_factory or (lambda i, s: make_fetchers(self.reports_dir, i, s))
        f_summary, f_spans = factory(iid, task.snapshot_id)
        traces, gaps, qwin, meta = read_all_traces(f_summary, f_spans)
        profile = profile_traces(traces, incident, read_gaps=gaps, query_window=qwin, metadata=meta)
        profile["incident_id"], profile["snapshot_id"] = iid, task.snapshot_id

        llm = None
        if self.use_llm and profile["sample"]["traces_count"] > 0:
            try:
                llm = self.llm or LLMClient.from_env()
            except LLMError:
                llm = None
        if task.follow_up:
            return answer_questions("traces", task, profile["patterns"], _describe, _link, "operation", llm, LLMError)
        return finding_to_agent_result(build_finding(profile, incident, llm), profile)
