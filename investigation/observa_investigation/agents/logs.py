"""Plug the finished Logs Agent (us01_logs_agent / package `logs_agent`) into the orchestrator.

finding_to_agent_result() is a pure mapping, so it is testable without the Logs Agent installed.
Nothing inside logs_agent needs to change.
"""
from typing import Callable, Optional

from ..contracts import EvidenceLink, MissingEvidence, SignalFinding
from .base import AgentResult, AgentTask


def evidence_id(incident_id: str, snapshot_id: str, row_id: str) -> str:
    """Stable opaque id: the F1 'logs' ref of this snapshot plus the profile row (P1, E2...). Proposed, confirm with F3."""
    return f"{incident_id}/{snapshot_id}/logs#{row_id}"


def _link(iid, sid, row_id, support) -> EvidenceLink:
    return EvidenceLink(evidence_id=evidence_id(iid, sid, row_id), relation="supports",
                        reference_valid=True, relevance_status="relevant", support_status=support)


def _describe(row: dict) -> str:
    """Deterministic sentence built only from the profile row: no model text, so it is a true observation."""
    if "category" in row:
        et = f" ({row['error_type']})" if row.get("error_type") else ""
        st = f", HTTP {row['status']}" if row.get("status") is not None else ""
        win = f" over {row['window_seconds']:g} s" if row.get("window_seconds") else ""
        return f"{row['service']} logged {row['count']} '{row['event']}' failure records{et}{st}{win} in the retained sample"
    return f"{row['service']} logged {row['count']} '{row['event']}' ({row['level']}) records in the retained sample"


def finding_to_agent_result(finding: dict, profile: dict) -> AgentResult:
    iid, sid, status = finding["incident_id"], finding["snapshot_id"], finding["status"]
    gaps = profile.get("read_gaps", [])
    records = profile["sample"]["records"]

    if status == "no_evidence" and gaps and records == 0:
        return AgentResult(status="failed", error="; ".join(gaps))  # a read failure, worth a retry

    missing = [MissingEvidence(required=f"log records from {svc}", requested_from="logs", status="unavailable",
                               reason=f"no log records from {svc} in the retained sample")
               for svc in profile.get("services_missing", [])]
    missing += [MissingEvidence(required="complete log sample", reason=g, requested_from="logs",
                                status="unavailable") for g in gaps]

    if status == "no_evidence":
        missing.insert(0, MissingEvidence(required="application logs for the incident", requested_from="logs",
                                          reason="no log evidence in this snapshot", status="unavailable"))
        return AgentResult(status="inconclusive", missing_evidence=missing)

    exec_status = "partial" if (missing or status != "ok") else "completed"
    notes = list(finding.get("limits", []))
    if status != "ok":
        notes.append(f"model interpretation not available ({status}); deterministic observations only")
    sample_note = "; ".join(notes) or None

    rows = {r["id"]: r for r in finding.get("evidence", [])}
    findings = [
        SignalFinding(finding_id=f"{iid}:logs:F{n}", signal_type="logs", service=row["service"],
                      claim=_describe(row), claim_kind="observation",
                      evidence_links=[_link(iid, sid, rid, "supported")],
                      execution_status=exec_status, uncertainty=sample_note)
        for n, (rid, row) in enumerate(rows.items(), 1)
    ]
    if status == "ok" and finding.get("suspected_service"):
        unc = f"confidence: {finding['confidence']}"
        if finding.get("caveats"):
            unc += "; " + " ".join(finding["caveats"])
        findings.append(SignalFinding(
            finding_id=f"{iid}:logs:F{len(findings) + 1}", signal_type="logs",
            service=finding["suspected_service"], claim=finding["hypothesis"], claim_kind="inference",
            evidence_links=[_link(iid, sid, rid, "unverified") for rid in rows],  # F3/F4 judge the support
            execution_status=exec_status, uncertainty=unc))
    return AgentResult(status=exec_status, findings=findings, missing_evidence=missing)


class LogsSignalAgent:
    signal = "logs"

    def __init__(self, reports_dir, llm=None, use_llm: bool = True,
                 fetcher_factory: Optional[Callable[[str, str], Callable]] = None):
        self.reports_dir, self.llm, self.use_llm = reports_dir, llm, use_llm
        self.fetcher_factory = fetcher_factory

    def run(self, task: AgentTask) -> AgentResult:
        from logs_agent.finding import build_finding
        from logs_agent.llm import LLMClient, LLMError
        from logs_agent.profiler import profile_logs
        from logs_agent.reader import make_fetcher, read_all_logs

        incident = task.incident
        iid = incident.get("id") or incident.get("incident_id")
        if not task.snapshot_id:
            return AgentResult(status="failed", error="incident has no snapshot_id")
        factory = self.fetcher_factory or (lambda i, s: make_fetcher(self.reports_dir, i, s))
        records, gaps = read_all_logs(factory(iid, task.snapshot_id))
        profile = profile_logs(records, incident, read_gaps=gaps)
        profile["incident_id"], profile["snapshot_id"] = iid, task.snapshot_id

        llm = None
        if self.use_llm and profile["sample"]["records"] > 0:
            try:
                llm = self.llm or LLMClient.from_env()
            except LLMError:
                llm = None  # no key/model configured: deterministic observations only
        return finding_to_agent_result(build_finding(profile, incident, llm), profile)
