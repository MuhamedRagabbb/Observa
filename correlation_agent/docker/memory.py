"""Look up approved incidents for one round of the F3 worker."""
import os

from observa.f5_memory.f1_adapter import incident_context_from_f1
from observa.f5_memory.f2_adapter import findings_from_investigation
from observa.f5_memory.f3_adapter import f3_memory_lookup
from observa.f5_memory.service import DEFAULT_DATABASE, IncidentMemory
from observa.f5_memory.validation import MemoryOperationError
from correlation_agent.agent.state import HistoricalMatch, MemorySearch


class WorkerMemoryLookup:
    def __init__(self, incident, investigations):
        self.incident = incident
        self.investigations = investigations
        self.summary = None

    def __call__(self, incident_id, services):
        try:
            context = incident_context_from_f1(self.incident)
            findings = [finding for report in self.investigations for finding in
                        findings_from_investigation(report, expected_incident_id=incident_id)]
            database = os.getenv("OBSERVA_MEMORY_DB", DEFAULT_DATABASE)
            memory = IncidentMemory(database, read_only=True)
            lookup = f3_memory_lookup(memory, {"incident": context, "findings": findings})
            notes = lookup(incident_id, services)
            result = lookup.last_result
            if result["status"] == "failed":
                self.summary = MemorySearch(status="unavailable", error_code=result["error"]["code"])
            else:
                matches = [HistoricalMatch(
                    source_incident_id=item["source_incident_id"], approved_report_id=item["approved_report_id"],
                    approved_version=item["approved_version"], evidence_ids=item["evidence_ids"],
                    matched_services=item["relevance_basis"]["matched_services"],
                    matched_terms=item["relevance_basis"]["matched_terms"]) for item in result["items"]]
                self.summary = MemorySearch(status="matched" if matches else "no_match", matches=matches)
        except MemoryOperationError as exc:
            self.summary = MemorySearch(status="unavailable", error_code=exc.code)
            notes = []
        print(f"[{incident_id}] F5 memory: {self.summary.status}, matches={len(self.summary.matches)}, "
              f"error={self.summary.error_code}")
        return notes
