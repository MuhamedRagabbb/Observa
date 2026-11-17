"""Plug the finished Metrics Agent (us02_metrics_agent / package `metrics_agent`) into the orchestrator.

finding_to_agent_result() is a pure mapping, so it is testable without the Metrics Agent installed.
"""
from typing import Callable, Optional

from ..contracts import EvidenceLink, MissingEvidence, SignalFinding
from .base import AgentResult, AgentTask


def evidence_id(incident_id: str, snapshot_id: str, row_id: str) -> str:
    """Stable opaque id: the F1 'metrics' ref of this snapshot plus the profile row (M1, M2...)."""
    return f"{incident_id}/{snapshot_id}/metrics#{row_id}"


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
    rule = row.get("rule") or "unknown"
    cat = row.get("category") or "metric"
    t_val = row.get("trigger_value")
    thresh = row.get("threshold")
    unit = row.get("unit") or ""
    trend = f", trend: {row['trend']}" if row.get("trend") else ""
    return f"{svc} breached {rule} ({cat}) with trigger value {t_val} vs threshold {thresh} {unit}{trend}"


def finding_to_agent_result(finding: dict, profile: dict) -> AgentResult:
    iid, sid, status = finding["incident_id"], finding["snapshot_id"], finding["status"]
    gaps = profile.get("read_gaps", [])
    records = profile.get("sample", {}).get("observations_count", 0)

    if status == "no_evidence" and gaps and records == 0:
        return AgentResult(status="failed", error="; ".join(gaps))

    missing = [
        MissingEvidence(
            required=f"metric observations from {svc}",
            requested_from="metrics",
            status="unavailable",
            reason=f"no metric observations from {svc}",
        )
        for svc in profile.get("services_missing", [])
    ]
    missing += [
        MissingEvidence(
            required="complete metrics sample",
            reason=g,
            requested_from="metrics",
            status="unavailable",
        )
        for g in gaps
    ]

    if status == "no_evidence":
        missing.insert(
            0,
            MissingEvidence(
                required="metric observations for the incident",
                requested_from="metrics",
                reason="no metric evidence in this snapshot",
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
            finding_id=f"{iid}:metrics:F{n}",
            signal_type="metrics",
            service=row["service"],
            claim=_describe(row),
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
                finding_id=f"{iid}:metrics:F{len(findings) + 1}",
                signal_type="metrics",
                service=finding["suspected_service"],
                claim=finding["hypothesis"],
                claim_kind="inference",
                evidence_links=[_link(iid, sid, rid, "unverified") for rid in rows],
                execution_status=exec_status,
                uncertainty=unc,
            )
        )
    return AgentResult(status=exec_status, findings=findings, missing_evidence=missing)


class MetricsSignalAgent:
    signal = "metrics"

    def __init__(
        self,
        reports_dir,
        llm=None,
        use_llm: bool = True,
        fetcher_factory: Optional[Callable[[str, str], Callable]] = None,
    ):
        self.reports_dir, self.llm, self.use_llm = reports_dir, llm, use_llm
        self.fetcher_factory = fetcher_factory

    def run(self, task: AgentTask) -> AgentResult:
        from metrics_agent.finding import build_finding
        from metrics_agent.llm import LLMClient, LLMError
        from metrics_agent.profiler import profile_metrics
        from metrics_agent.reader import make_fetcher, read_all_metrics

        incident = task.incident
        iid = incident.get("id") or incident.get("incident_id")
        if not task.snapshot_id:
            return AgentResult(status="failed", error="incident has no snapshot_id")
        factory = self.fetcher_factory or (lambda i, s: make_fetcher(self.reports_dir, i, s))
        obs, gaps, qwin, meta = read_all_metrics(factory(iid, task.snapshot_id))
        profile = profile_metrics(obs, incident, read_gaps=gaps, query_window=qwin, metadata=meta)
        profile["incident_id"], profile["snapshot_id"] = iid, task.snapshot_id

        llm = None
        if self.use_llm and profile["sample"]["observations_count"] > 0:
            try:
                llm = self.llm or LLMClient.from_env()
            except LLMError:
                llm = None
        return finding_to_agent_result(build_finding(profile, incident, llm), profile)
