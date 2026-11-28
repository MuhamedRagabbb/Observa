"""Empty-safe F5 approved historical incident memory adapter for F4."""
from __future__ import annotations

from typing import Any, Callable

from .contracts import ApprovedKnowledge, CorrelationContext, ErrorEnvelope, MemorySearchResult

MemoryLookup = Callable[[str, list[str]], MemorySearchResult | dict[str, Any] | list[dict] | list[ApprovedKnowledge]]


def _normalize_approved_item(item: ApprovedKnowledge | dict[str, Any]) -> ApprovedKnowledge:
    if isinstance(item, ApprovedKnowledge):
        return item
    data = dict(item)
    incident_ctx = data.get("incident_context") or {}
    if not data.get("affected_services") and isinstance(incident_ctx, dict):
        data["affected_services"] = list(incident_ctx.get("affected_services") or [])
    if not data.get("reviewed_assessment"):
        data["reviewed_assessment"] = str(data.get("executive_summary") or "Approved historical incident")
    if data.get("verified_root_cause") is None and isinstance(data.get("final_root_cause"), dict):
        desc = data["final_root_cause"].get("description")
        if desc:
            data["verified_root_cause"] = str(desc)
    return ApprovedKnowledge.model_validate(data)


def f5_memory_lookup(
    memory: Any,
    *,
    query: str = "",
    limit: int = 5,
    min_matched_terms: int | None = 0,
) -> MemoryLookup:
    """Adapt ``observa.f5_memory.IncidentMemory`` to F4's ``MemoryLookup`` callable."""

    def _lookup(incident_id: str, services: list[str]) -> dict[str, Any]:
        effective_min_terms = min_matched_terms if query == "" else None
        return memory.search_approved_incidents(
            query=query,
            affected_services=services,
            limit=limit,
            exclude_incident_id=incident_id,
            min_matched_terms=effective_min_terms,
        )

    return _lookup


def search_approved_incidents(
    context: CorrelationContext,
    lookup: MemoryLookup | None = None,
) -> MemorySearchResult:
    """Query F5 approved historical records safely.

    An absent lookup or empty match list returns status='ok' with items=[].
    Retrieval errors return status='failed' with a typed ErrorEnvelope instead of
    masquerading as an empty result.
    """
    if lookup is None:
        return MemorySearchResult(status="ok", items=[], error=None)
    services = sorted(set(context.affected_services))
    try:
        raw = lookup(context.incident_id, services)
    except Exception as exc:
        return MemorySearchResult(
            status="failed",
            items=[],
            error=ErrorEnvelope(
                code="STORAGE_FAILURE",
                message=f"F5 memory lookup failed: {exc}",
                retryable=True,
                operation_id=f"MEM-{context.correlation_run_id}",
                details={"error_type": type(exc).__name__},
            ),
        )
    if isinstance(raw, MemorySearchResult):
        # Enforce approved-only invariant even if a caller passes unapproved data.
        approved_items = [item for item in raw.items if item.is_current_approved_version]
        return MemorySearchResult(status=raw.status, items=approved_items, error=raw.error)
    if isinstance(raw, dict):
        status = "failed" if raw.get("status") == "failed" else "ok"
        raw_err = raw.get("error")
        err = ErrorEnvelope.model_validate(raw_err) if isinstance(raw_err, dict) else raw_err
        items = [_normalize_approved_item(item) for item in (raw.get("items") or [])]
        approved_items = [item for item in items if item.is_current_approved_version]
        return MemorySearchResult(status=status, items=approved_items, error=err)
    items = [_normalize_approved_item(item) for item in raw]
    approved_items = [item for item in items if item.is_current_approved_version]
    return MemorySearchResult(status="ok", items=approved_items, error=None)
