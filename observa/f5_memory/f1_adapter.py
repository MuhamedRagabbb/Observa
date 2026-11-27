"""Adapter from Feature 1's incident file to the incident context Feature 5 stores.

Feature 1's branch writes ``incident.json`` with ``id``, ``summary``, ``first_observed_at``,
``resolved_at`` and alerts keyed by alert ID. The architecture diagram and F1-US-03 name the
same facts ``incident_id``, ``title``, ``start_time``, ``end_time`` and ``alerts``. Which
names are final is not agreed, so this adapter is a proposal: it accepts the branch shape
and returns the diagram shape. It is a pure function. It reads no file, network or telemetry.

Unknown stays unknown: a missing ``resolved_at`` gives ``end_time: None``, and a missing
summary is a validation error, never an invented title.
"""

from .validation import MemoryOperationError, optional_text, require, strings, text, window

SUPPORTED_F1_SCHEMA_VERSIONS = ("1.0", "2.0")


def incident_context_from_f1(f1_incident):
    """Return ``{incident_id, title, alerts, start_time, end_time, affected_services}``.

    ``alerts`` becomes a sorted list of alert IDs, because that is what memory stores today.
    Feature 1's per-alert detail (rule, service, severity, values) is not carried over.
    Feature 1's ``root_cause`` and ``handoff.hypotheses`` are ignored on purpose: memory
    takes a root cause only from an approved report.
    """
    try:
        require(isinstance(f1_incident, dict), "F1 incident must be an object")
        version = f1_incident["schema_version"]
        require(version in SUPPORTED_F1_SCHEMA_VERSIONS,
                f"Unsupported F1 schema_version {version!r}; supported: {', '.join(SUPPORTED_F1_SCHEMA_VERSIONS)}")
        text(f1_incident["id"], "id")
        text(f1_incident.get("summary"), "summary")
        alerts = f1_incident["alerts"]
        require(isinstance(alerts, dict), "alerts must be an object keyed by alert ID")
        for alert_id, alert in alerts.items():
            text(alert_id, "alert ID")
            require(isinstance(alert, dict) and alert.get("id") == alert_id, "alert key differs from its id")
        services = f1_incident["affected_services"]
        strings(services, "affected_services")
        resolved_at = f1_incident.get("resolved_at")
        optional_text(resolved_at, "resolved_at")
        context = {
            "incident_id": f1_incident["id"],
            "title": f1_incident["summary"],
            "alerts": sorted(alerts),
            "start_time": f1_incident["first_observed_at"],
            "end_time": resolved_at,
            "affected_services": list(services),
        }
        window(context)
    except (KeyError, TypeError) as exc:
        raise MemoryOperationError("VALIDATION_ERROR", f"Malformed F1 incident: {exc}") from None
    return context


def alert_findings_from_f1(f1_incident):
    """Turn F1's alerts into search terms for ``check_memory``: one entry per alert.

    F1 titles are generic ("Observed payment symptoms in payments-service"), so the alert
    rule names (``pool_timeouts``, ``payment_failures``) are what makes a match possible
    before F2 has run. Each entry is ``{"claim", "indicator", "service"}``; ``claim`` is F1's
    own alert title. These are F1 detections, not F2 findings, and they are never stored.
    """
    try:
        require(isinstance(f1_incident, dict), "F1 incident must be an object")
        alerts = f1_incident["alerts"]
        require(isinstance(alerts, dict), "alerts must be an object keyed by alert ID")
        findings = []
        for alert_id in sorted(alerts):
            alert = alerts[alert_id]
            require(isinstance(alert, dict), "alert must be an object")
            for key in ("rule", "service", "title"):
                text(alert.get(key), f"alert {key}")
            findings.append({"claim": alert["title"], "indicator": alert["rule"], "service": alert["service"]})
    except (KeyError, TypeError) as exc:
        raise MemoryOperationError("VALIDATION_ERROR", f"Malformed F1 incident: {exc}") from None
    return findings
