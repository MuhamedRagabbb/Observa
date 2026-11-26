"""Executable schema for stored approvals (format 3). See docs/feature-5.md for the field list.

A stored approval is the exact Feature 7 report version a Feature 6 reviewer approved,
plus that decision. F6 and F7 code exists only on their shared branch (2026-11-25, no PR),
so the shape checked here follows their code and is still a proposal, not a merged contract.
"""

import hashlib
import json
import re
from datetime import datetime, timedelta


class MemoryOperationError(Exception):
    def __init__(self, code, message, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable

    def envelope(self, operation_id):
        return {"code": self.code, "message": str(self), "retryable": self.retryable,
                "operation_id": operation_id, "details": None}


def require(condition, message, code="VALIDATION_ERROR"):
    if not condition:
        raise MemoryOperationError(code, message)


def canonical(value):
    """Sorted keys, compact separators, UTF-8 text: the same bytes Feature 7 hashes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def content_digest(content):
    """SHA-256 of canonical JSON. Matches F7's ``observa_reports.content.digest``."""
    return hashlib.sha256(canonical(content).encode("utf-8")).hexdigest()


REPORT_SCHEMA_VERSIONS = ("1.0",)
SIGNALS = ("logs", "metrics", "traces")
SEVERITIES = ("low", "medium", "high", "critical")
DIGEST = re.compile(r"^[0-9a-f]{64}$")


def text(value, name):
    require(isinstance(value, str) and bool(value.strip()), f"{name} must be a nonempty string")


def string(value, name):
    require(isinstance(value, str), f"{name} must be a string")


def integer(value, name):
    require(type(value) is int and 1 <= value <= 2**63 - 1, f"{name} must be a positive signed 64-bit integer")


def optional_text(value, name):
    if value is not None:
        text(value, name)


def strings(value, name):
    require(isinstance(value, list), f"{name} must be a list")
    for item in value:
        text(item, name)


def one_of(value, allowed, name):
    require(isinstance(value, str) and value in allowed, f"{name} must be one of {', '.join(allowed)}")


def objects(value, name):
    require(isinstance(value, list) and all(isinstance(item, dict) for item in value), f"{name} must be a list of objects")
    return value


def timestamp(value, name):
    text(value, name)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise MemoryOperationError("VALIDATION_ERROR", f"{name} must be an ISO 8601 timestamp") from None
    require(parsed.utcoffset() == timedelta(0), f"{name} must have an explicit UTC timezone")
    return parsed


def window(obj):
    start = timestamp(obj["start_time"], "start_time")
    if obj["end_time"] is not None:
        require(timestamp(obj["end_time"], "end_time") >= start, "end_time precedes start_time")


def digest_value(value, name):
    require(isinstance(value, str) and DIGEST.match(value) is not None, f"{name} must be 64 lowercase hex characters")


def evidence_links(value, name):
    for item in objects(value, name):
        text(item["evidence_id"], f"{name}.evidence_id")
        one_of(item["relation"], ("supports", "contradicts", "context_only", "unknown"), f"{name}.relation")
        valid = item["reference_valid"]
        require(valid is True or valid is False or valid == "unknown", f"{name}.reference_valid must be true, false or unknown")
        one_of(item["relevance_status"], ("relevant", "irrelevant", "unknown"), f"{name}.relevance_status")
        one_of(item["support_status"], ("supported", "contradicted", "unverified"), f"{name}.support_status")


def _cause(cause, name):
    require(isinstance(cause, dict), f"{name} must be an object")
    text(cause["candidate_id"], f"{name}.candidate_id")
    text(cause["description"], f"{name}.description")
    optional_text(cause["uncertainty"], f"{name}.uncertainty")
    evidence_links(cause["evidence_links"], f"{name}.evidence_links")


def _recommendation(rec, name):
    if rec["rank"] is not None:
        integer(rec["rank"], f"{name}.rank")
    text(rec["recommendation_id"], f"{name}.recommendation_id")
    text(rec["action"], f"{name}.action")
    one_of(rec["action_type"], ("remediation", "verification", "investigation"), f"{name}.action_type")
    one_of(rec["validation"], ("supported", "unsupported", "conditional"), f"{name}.validation")
    string(rec["rationale"], f"{name}.rationale")
    optional_text(rec["risk_or_caveat"], f"{name}.risk_or_caveat")
    evidence_links(rec["evidence_links"], f"{name}.evidence_links")


def validate_report_content(content):
    """Check the parts of F7 ``ReportContent`` that memory reads. Unknown values stay null."""
    require(isinstance(content, dict), "report content must be an object")
    require(content["schema_version"] in REPORT_SCHEMA_VERSIONS,
            f"Unsupported report schema_version {content['schema_version']!r}", "SCHEMA_MISMATCH")
    overview = content["overview"]
    require(isinstance(overview, dict), "overview must be an object")
    text(overview["incident_id"], "overview.incident_id")
    text(overview["title"], "overview.title")
    strings(overview["affected_services"], "overview.affected_services")
    window(overview)
    require(overview["severity"] is None or overview["severity"] in SEVERITIES, "overview.severity is invalid")
    string(content["executive_summary"], "executive_summary")
    alerts = content["alerts"]
    for item in objects(alerts["items"], "alerts.items"):
        text(item["alert_id"], "alerts.items.alert_id")
    strings(alerts["missing_alert_ids"], "alerts.missing_alert_ids")
    for signal in SIGNALS:
        section = content["findings"][signal]
        text(section["status"], f"findings.{signal}.status")
        for item in objects(section["findings"], f"findings.{signal}.findings"):
            for key in ("finding_id", "service", "claim", "execution_status"):
                text(item[key], f"findings.{signal}.{key}")
            one_of(item["claim_kind"], ("observation", "inference"), f"findings.{signal}.claim_kind")
            optional_text(item["indicator"], f"findings.{signal}.indicator")
            optional_text(item["uncertainty"], f"findings.{signal}.uncertainty")
            evidence_links(item["evidence_links"], f"findings.{signal}.evidence_links")
    root = content["root_cause"]
    one_of(root["status"], ("leading", "inconclusive", "unavailable"), "root_cause.status")
    require((root["status"] == "leading") == (root["leading"] is not None),
            "root_cause.leading must be set exactly when status is leading")
    if root["leading"] is not None:
        _cause(root["leading"], "root_cause.leading")
    for cause in objects(root["alternatives"], "root_cause.alternatives"):
        _cause(cause, "root_cause.alternatives")
    ids = []
    for entry in objects(content["evidence_register"], "evidence_register"):
        text(entry["evidence_id"], "evidence_register.evidence_id")
        require(entry["signal_type"] is None or entry["signal_type"] in SIGNALS, "evidence_register.signal_type is invalid")
        optional_text(entry["snapshot_id"], "evidence_register.snapshot_id")
        strings(entry["services"], "evidence_register.services")
        one_of(entry["reference_status"], ("valid", "invalid", "unresolved"), "evidence_register.reference_status")
        objects(entry["assessments"], "evidence_register.assessments")
        ids.append(entry["evidence_id"])
    require(len(ids) == len(set(ids)), "Duplicate evidence IDs")
    for item in objects(content["missing_evidence"], "missing_evidence"):
        one_of(item["source"], ("F2", "F3"), "missing_evidence.source")
        for key in ("required", "reason", "requested_from", "status"):
            text(item[key], f"missing_evidence.{key}")
    recs = content["recommendations"]
    one_of(recs["status"], ("ranked", "no_validated_remediation", "unavailable"), "recommendations.status")
    optional_text(recs["recommendation_status"], "recommendations.recommendation_status")
    for key in ("ranked", "other_options"):
        for rec in objects(recs[key], f"recommendations.{key}"):
            _recommendation(rec, f"recommendations.{key}")
    require((recs["status"] == "ranked") == bool(recs["ranked"]), "recommendations.ranked disagrees with its status")
    strings(recs["caveats"], "recommendations.caveats")
    strings(content["limitations"], "limitations")
    string(content["draft_notice"], "draft_notice")


REPORT_KEYS = {"report_id", "incident_id", "version", "previous_version", "content_ref", "content_digest",
               "source_investigation_run_id", "created_at", "review_cycle_id", "evidence_ids",
               "uncertainty_flags", "content"}
DECISION_KEYS = {"decision_id", "idempotency_key", "incident_id", "report_id", "version", "reviewer_id",
                 "decision", "feedback", "decided_at"}


def validate_snapshot(snapshot, decision_id):
    """Validate a snapshot resolved by the trusted approval source, never user assertions.

    ``{"report": <F7 ReportVersion fields + content>, "decision": <F6 ReviewDecision>}``,
    plus an optional ``"synthetic": true`` label on fake data.
    """
    try:
        require(isinstance(snapshot, dict), "approval snapshot must be an object")
        require(set(snapshot) - {"synthetic"} == {"report", "decision"}, "snapshot must hold exactly report and decision")
        require(snapshot.get("synthetic", True) is True, "synthetic must be true when present")
        report, decision = snapshot["report"], snapshot["decision"]
        require(isinstance(report, dict) and isinstance(decision, dict), "snapshot sections must be objects")
        require(set(decision) == DECISION_KEYS, "decision must have exactly the ReviewDecision keys")
        for key in ("decision_id", "idempotency_key", "incident_id", "report_id", "reviewer_id"):
            text(decision[key], f"decision.{key}")
        require(decision["decision_id"] == decision_id, "Resolved decision ID differs from requested ID")
        require(decision["decision"] == "approve", "Only explicit approvals can enter memory", "NOT_APPROVED")
        optional_text(decision["feedback"], "feedback")
        timestamp(decision["decided_at"], "decided_at")
        integer(decision["version"], "decision.version")
        require(set(report) == REPORT_KEYS, "report must have exactly the report version keys and content")
        text(report["report_id"], "report_id")
        text(report["incident_id"], "report.incident_id")
        integer(report["version"], "report.version")
        previous = report["previous_version"]
        require((previous is None and report["version"] == 1)
                or (type(previous) is int and previous == report["version"] - 1),
                "previous_version must be version - 1, or null for version 1")
        digest_value(report["content_digest"], "content_digest")
        require(report["content_ref"] == f"sha256:{report['content_digest']}", "content_ref must name content_digest")
        text(report["source_investigation_run_id"], "source_investigation_run_id")
        timestamp(report["created_at"], "report.created_at")
        optional_text(report["review_cycle_id"], "review_cycle_id")
        strings(report["evidence_ids"], "evidence_ids")
        strings(report["uncertainty_flags"], "uncertainty_flags")
        content = report["content"]
        validate_report_content(content)
        require(report["incident_id"] == decision["incident_id"] == content["overview"]["incident_id"],
                "Incident IDs differ")
        require(report["report_id"] == decision["report_id"] and report["version"] == decision["version"],
                "Approval does not identify this exact report version", "VERSION_CONFLICT")
        require(report["content_digest"] == content_digest(content), "Report content digest mismatch", "VERSION_CONFLICT")
        canonical(snapshot)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise MemoryOperationError("VALIDATION_ERROR", f"Malformed approval snapshot: {exc}") from None
    return snapshot
