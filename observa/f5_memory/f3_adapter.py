"""Adapter that fits Feature 3's current memory hook without changing F3's code.

F3 on ``main`` (``correlation_agent/agent/tools.py``) declares
``MemoryLookup = Callable[[str, list[str]], list[str]]`` and calls
``lookup(report.incident_id, sorted(services))``. The strings it gets back go into the LLM
prompt as ``history`` (``graph.py``), which F3's prompt treats as context only.

``f3_memory_lookup(memory, context)`` returns such a callable. Because F3 passes only an ID
and service names, the adapter needs the query text from elsewhere: the caller gives it the
current incident context and F2 findings (the Docker worker already holds both when it calls
``correlate``). Without that context there is nothing to match on, and it returns ``[]``.

What this adapter cannot do, because of F3's signature:

- **Tell "no match" from "failed" through the return value.** Both are ``[]``. The full
  ``{"status", "items", "error"}`` result of the last call is kept on ``lookup.last_result``
  for the caller to log or record, but F3 itself never sees it.
- **Give F3 structured provenance.** Each string names its source incident, report version
  and approval, and says it is historical, but F3 cannot read IDs or reasons from it.
- **Raise.** Any memory error is turned into ``[]`` so correlation continues on current
  evidence (IKM-08). The error is kept in ``last_result``.

The strings are sent to F3's LLM provider (Groq). Whether that is acceptable is a team decision.
"""

from collections.abc import Callable

from .f2_adapter import findings_from_investigation
from .validation import MemoryOperationError, require, strings, text

MAX_RECOMMENDATIONS_PER_ITEM = 2


def format_for_prompt(item):
    """One line per historical match, labelled as approved history and never as current evidence."""
    cause = item["final_root_cause"]["description"] if item["final_root_cause"] else "no leading cause was established"
    recs = "; ".join(r["action"] for r in item["accepted_recommendations"][:MAX_RECOMMENDATIONS_PER_ITEM]) or "none"
    basis = item["relevance_basis"]
    return (f"[HISTORICAL, human-approved] {item['source_incident_id']} report {item['approved_report_id']} "
            f"v{item['approved_version']} (approved {item['approved_at']}). Past conclusion: {cause} "
            f"Past recommendations (outcome unknown): {recs}. Matched on services "
            f"{basis['matched_services'] or 'none'} and terms {basis['matched_terms']}. "
            "Context only: not evidence for the current incident.")


class F3MemoryLookup:
    """Callable with F3's ``MemoryLookup`` signature. See the module docstring for its limits."""

    def __init__(self, memory, context=None, limit=3):
        require(context is None or isinstance(context, dict) or callable(context),
                "context must be a dict, a callable incident_id -> dict, or None")
        require(type(limit) is int and 1 <= limit <= 10, "limit must be between 1 and 10")
        self._memory, self._context, self._limit = memory, context, limit
        self.last_result = None

    def _context_for(self, incident_id):
        context = self._context(incident_id) if callable(self._context) else self._context
        if context is None:
            return None, []
        require(isinstance(context, dict), "context must be an object")
        incident = context["incident"]
        require(isinstance(incident, dict) and incident.get("incident_id") == incident_id,
                "context belongs to a different incident")
        findings = list(context.get("findings", []))
        if context.get("investigation") is not None:
            findings += findings_from_investigation(context["investigation"])
        return incident, findings

    def __call__(self, incident_id: str, services: list[str]) -> list[str]:
        try:
            text(incident_id, "incident_id")
            strings(services, "services")
            incident, findings = self._context_for(incident_id)
            if incident is None:
                self.last_result = {"status": "ok", "items": [], "error": None,
                                    "note": "No incident context was given, so there was no query text."}
                return []
            merged = {**incident, "affected_services": sorted({*incident["affected_services"], *services})}
            self.last_result = self._memory.check_memory(merged, findings, self._limit)
        except MemoryOperationError as exc:
            self.last_result = {"status": "failed", "items": [], "error": exc.envelope(f"f3-lookup:{incident_id}")}
        except (KeyError, TypeError, ValueError) as exc:
            self.last_result = {"status": "failed", "items": [], "error": {
                "code": "VALIDATION_ERROR", "message": f"Bad lookup context: {exc}", "retryable": False,
                "operation_id": f"f3-lookup:{incident_id}", "details": None}}
        return [format_for_prompt(item) for item in self.last_result["items"]]


def f3_memory_lookup(memory, context: dict | Callable | None = None, limit=3) -> F3MemoryLookup:
    """Build the callable F3's ``correlate(..., memory_lookup=...)`` accepts.

    ``context``: ``{"incident": <incident context>, "findings": [...], "investigation": <F2 result>}``
    (``findings`` and ``investigation`` optional), or a callable ``incident_id -> that dict``.
    """
    return F3MemoryLookup(memory, context, limit)
