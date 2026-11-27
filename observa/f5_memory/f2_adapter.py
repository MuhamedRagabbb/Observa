"""Adapter from Feature 2's ``InvestigationResult`` to ``check_memory`` input.

F2 (``investigation/observa_investigation/contracts.py`` on ``main``, labelled PROPOSED)
returns one ``SignalOutput`` per signal (``logs_output``, ``metrics_output``,
``traces_output``), each with ``SignalFinding`` objects. This module reads that JSON (or the
pydantic model) and returns the finding fields memory searches on. It reads no files and
stores nothing: current findings are used only as the query, never written into memory.
"""

from .validation import SIGNALS, MemoryOperationError, optional_text, require, text


def findings_from_investigation(investigation, *, expected_incident_id=None):
    """Return ``[{"finding_id", "signal_type", "service", "claim", "claim_kind", "indicator"}]``."""
    if hasattr(investigation, "model_dump"):
        investigation = investigation.model_dump(mode="json")
    try:
        require(isinstance(investigation, dict), "investigation must be an object")
        text(investigation["incident_id"], "investigation.incident_id")
        if expected_incident_id is not None:
            require(investigation["incident_id"] == expected_incident_id,
                    "incident context and investigation belong to different incidents")
        findings = []
        for signal in SIGNALS:
            output = investigation[f"{signal}_output"]
            require(isinstance(output, dict) and isinstance(output["findings"], list),
                    f"{signal}_output must hold a findings list")
            for finding in output["findings"]:
                require(isinstance(finding, dict), "finding must be an object")
                for key in ("finding_id", "service", "claim"):
                    text(finding[key], f"{signal} finding {key}")
                optional_text(finding.get("indicator"), f"{signal} finding indicator")
                findings.append({"finding_id": finding["finding_id"], "signal_type": signal,
                                 "service": finding["service"], "claim": finding["claim"],
                                 "claim_kind": finding.get("claim_kind"), "indicator": finding.get("indicator")})
    except (KeyError, TypeError) as exc:
        raise MemoryOperationError("VALIDATION_ERROR", f"Malformed F2 investigation: {exc}") from None
    return findings


def check_memory_for_investigation(memory, incident_context, investigation, limit=5, extra_findings=()):
    """``check_memory`` with the current incident context and F2's findings as the query."""
    findings = findings_from_investigation(investigation)
    incident_id = (investigation.model_dump(mode="json") if hasattr(investigation, "model_dump")
                   else investigation)["incident_id"]
    require(isinstance(incident_context, dict) and incident_context.get("incident_id") == incident_id,
            "incident context and investigation belong to different incidents")
    return memory.check_memory(incident_context, [*findings, *extra_findings], limit)
