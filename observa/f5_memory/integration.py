"""Proposed read-side connection points for F3 and F4.

F3 is on ``main`` with its own memory lookup in a different shape (see docs/feature-5.md);
F4 has no code. These functions return new dictionaries and never decide a current root
cause or missing evidence. The write path from F6 is ``f6_handoff.IncidentMemorySink``;
F3's current hook is served by ``f3_adapter.f3_memory_lookup``.
"""

from .validation import require


def populate_correlation_state(state, memory, limit=5):
    """F3: add relevant_data_from_memory using current incident context and findings."""
    result = memory.check_memory(state["incident_context"], state.get("correlated_findings", []), limit)
    return {**state, "relevant_data_from_memory": result}


def similar_incidents_for_recommendation(correlation_state, memory, limit=5):
    """F4: look up past incidents using the current leading cause. No match or failure gives no items.

    ``final_root_cause`` may be F3's internal ``Candidate`` (``hypothesis``) or the public
    contract candidate (``description``).
    """
    root_cause = correlation_state.get("final_root_cause")
    findings = []
    if root_cause:
        statement = root_cause.get("description") or root_cause.get("hypothesis")
        findings = [statement] if statement else []
    return memory.check_memory(correlation_state["incident_context"], findings, limit)


def similar_incidents_for_correlation_result(memory, incident_context, correlation_result, investigation=None, limit=5):
    """F4: past approved incidents for F3's public output (``f3/output.json``).

    Reads ``leading_candidate_id`` and ``root_cause_candidates[].description`` from F3's
    ``ContractCorrelationResult``; adds F2 findings when given. Each item carries the past
    report's ``accepted_recommendations`` and ``other_recommendation_options`` (with
    ``execution_status`` and ``resolution_status`` still ``unknown``). These are history for
    F4 to weigh, not recommendations for the current incident.
    """
    from .f2_adapter import findings_from_investigation

    require(isinstance(correlation_result, dict), "correlation_result must be an object")
    require(correlation_result.get("incident_id") == incident_context.get("incident_id"),
            "correlation result and incident context belong to different incidents")
    candidates = {c.get("candidate_id"): c for c in correlation_result.get("root_cause_candidates", [])
                  if isinstance(c, dict)}
    leading = candidates.get(correlation_result.get("leading_candidate_id"))
    findings = [leading["description"]] if leading and leading.get("description") else []
    if investigation is not None:
        findings += findings_from_investigation(investigation)
    return memory.check_memory(incident_context, findings, limit)
