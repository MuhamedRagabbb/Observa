"""SQLite-backed Feature 5 domain operations (Python standard library only)."""

import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .relevance import KeywordScorer
from .sources import TrustedApprovalSource
from .validation import (SIGNALS, MemoryOperationError, canonical, content_digest, integer,
                         require, strings, text, validate_snapshot)

# Format 3 stores the exact F7 report version a F6 reviewer approved. Format 2 (the earlier
# prototype shape with string findings) is not migrated: an old file gives SCHEMA_MISMATCH.
SCHEMA_VERSION = 3
DEFAULT_DATABASE = "data/incident-memory-v3.sqlite3"
# check_memory sends long incident text, so one shared word is too weak a signal.
CONTEXT_MIN_MATCHED_TERMS = 2
# No feature reports whether a recommendation was carried out or whether it worked.
UNKNOWN_OUTCOME = {"execution_status": "unknown", "resolution_status": "unknown"}


def _with_outcome(recommendations):
    return [{**copy.deepcopy(rec), **UNKNOWN_OUTCOME} for rec in recommendations]


def knowledge_record(snapshot):
    """The read view of one stored approval. Derived from the stored snapshot, never stored itself."""
    report, decision = snapshot["report"], snapshot["decision"]
    content = copy.deepcopy(report["content"])
    overview, root, recs = content["overview"], content["root_cause"], content["recommendations"]
    alerts = content["alerts"]
    findings = [{"signal_type": signal, **item} for signal in SIGNALS for item in content["findings"][signal]["findings"]]
    return {
        "knowledge_id": None,
        "source_incident_id": overview["incident_id"],
        "approved_report_id": report["report_id"], "approved_version": report["version"],
        "approval_decision_id": decision["decision_id"], "approved_at": decision["decided_at"],
        "reviewer_id": decision["reviewer_id"], "reviewer_comment": decision["feedback"],
        "report_content_ref": report["content_ref"], "report_created_at": report["created_at"],
        "incident_context": {
            "incident_id": overview["incident_id"], "title": overview["title"],
            "alerts": sorted({a["alert_id"] for a in alerts["items"]} | set(alerts["missing_alert_ids"])),
            "start_time": overview["start_time"], "end_time": overview["end_time"],
            "affected_services": overview["affected_services"], "severity": overview["severity"],
        },
        "executive_summary": content["executive_summary"],
        "findings": findings,
        "signal_status": {signal: content["findings"][signal]["status"] for signal in SIGNALS},
        "root_cause_status": root["status"],
        "final_root_cause": root["leading"],
        "alternative_root_causes": root["alternatives"],
        "verified_root_cause": None,
        "accepted_recommendations": _with_outcome(recs["ranked"]),
        "other_recommendation_options": _with_outcome(recs["other_options"]),
        "recommendation_status": recs["status"],
        "recommendation_caveats": recs["caveats"],
        "evidence_ids": report["evidence_ids"],
        "evidence_refs": content["evidence_register"],
        "unavailable_evidence_ids": [e["evidence_id"] for e in content["evidence_register"]
                                     if e["reference_status"] != "valid"],
        "missing_evidence": content["missing_evidence"],
        "limitations": content["limitations"],
        "uncertainty_flags": report["uncertainty_flags"],
        "knowledge_origin": "historical",
    }


def _comparison(record, findings, services):
    """IKM-04: what the past and current incidents share and where they differ, by name only."""
    current_indicators = {f["indicator"].casefold() for f in findings if isinstance(f, dict) and f.get("indicator")}
    current_signals = {f["signal_type"] for f in findings if isinstance(f, dict) and f.get("signal_type")}
    past_indicators = {f["indicator"].casefold() for f in record["findings"] if f["indicator"]}
    past_signals = {f["signal_type"] for f in record["findings"]}
    basis = record["relevance_basis"]
    return {
        "services": {"shared": basis["matched_services"], "historical_only": basis["historical_only_services"],
                     "current_only": basis["current_only_services"]},
        "indicators": {"shared": sorted(past_indicators & current_indicators),
                       "historical_only": sorted(past_indicators - current_indicators),
                       "current_only": sorted(current_indicators - past_indicators)},
        "signals_with_findings": {"historical": sorted(past_signals), "current": sorted(current_signals)},
        "note": "Names compared only. A shared indicator is a similarity to check, not evidence for the current cause.",
    }


class IncidentMemory:
    def __init__(self, database=DEFAULT_DATABASE, approval_source: TrustedApprovalSource | None = None, scorer=None,
                 require_ended_incident=False):
        """``require_ended_incident``: store only reports whose incident has an end time.

        Off by default, which keeps "approval alone is enough". Whether closure is also
        required is an open team decision; F6 itself lets a reviewer approve an open incident.
        """
        self.database = Path(database).resolve()
        self.approval_source = approval_source
        self.scorer = scorer or KeywordScorer()
        require(isinstance(require_ended_incident, bool), "require_ended_incident must be true or false")
        self.require_ended_incident = require_ended_incident
        try:
            self.database.parent.mkdir(parents=True, exist_ok=True)
            with self._connection(create=True) as db:
                if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    self._initialize_schema(db)
        except OSError:
            raise MemoryOperationError("STORAGE_FAILURE", "Cannot initialize memory storage", True) from None

    def _initialize_schema(self, db):
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        has_table = db.execute("SELECT 1 FROM sqlite_master WHERE name='knowledge'").fetchone()
        require(version == 0 and has_table is None,
                f"{self.database} uses an older memory format; use another --db path or remove it and seed again",
                "SCHEMA_MISMATCH")
        db.execute("""CREATE TABLE knowledge (
            knowledge_id TEXT PRIMARY KEY,
            incident_id TEXT NOT NULL,
            report_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            decision_id TEXT NOT NULL UNIQUE,
            idempotency_key TEXT NOT NULL UNIQUE,
            snapshot TEXT NOT NULL,
            UNIQUE(incident_id, version), UNIQUE(report_id, version)
        )""")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connection(self, create=False):
        db = None
        try:
            uri = self.database.as_uri() + ("?mode=rwc" if create else "?mode=rw")
            db = sqlite3.connect(uri, uri=True, timeout=5)
            db.row_factory = sqlite3.Row
            with db:
                yield db
        except sqlite3.Error:
            raise MemoryOperationError("STORAGE_FAILURE", "Memory storage operation failed; retry safely", True) from None
        finally:
            if db is not None:
                db.close()

    @staticmethod
    def _record(row, current_version):
        try:
            snapshot = json.loads(row["snapshot"])
            validate_snapshot(snapshot, row["decision_id"])
            require(snapshot["report"]["incident_id"] == row["incident_id"]
                    and snapshot["report"]["report_id"] == row["report_id"]
                    and snapshot["report"]["version"] == row["version"],
                    "Stored snapshot identity mismatch")
        except (ValueError, TypeError, KeyError, MemoryOperationError):
            raise MemoryOperationError("STORAGE_FAILURE", "Stored knowledge failed integrity validation") from None
        record = knowledge_record(snapshot)
        record["knowledge_id"] = row["knowledge_id"]
        record["record_version"] = row["version"]
        record["is_current_approved_version"] = row["version"] == current_version
        return record

    @staticmethod
    def _current_version(db, incident_id):
        return db.execute("SELECT MAX(version) FROM knowledge WHERE incident_id=?", (incident_id,)).fetchone()[0]

    def save_approved_incident(self, decision_id):
        """Resolve approval through the injected trusted port. Errors are typed exceptions."""
        text(decision_id, "decision_id")
        require(self.approval_source is not None, "Writes require a trusted approval source", "NOT_AUTHORIZED")
        snapshot = validate_snapshot(self.approval_source.resolve(decision_id), decision_id)
        report, decision = snapshot["report"], snapshot["decision"]
        require(not self.require_ended_incident or report["content"]["overview"]["end_time"] is not None,
                "This memory stores only reports of incidents that have ended", "NOT_ELIGIBLE")
        incident_id = report["incident_id"]
        payload = canonical(snapshot)
        knowledge_id = "IKM-" + content_digest([incident_id, report["report_id"], report["version"]])
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("""SELECT * FROM knowledge WHERE decision_id=? OR idempotency_key=?
                OR (incident_id=? AND version=?) OR (report_id=? AND version=?)""",
                (decision_id, decision["idempotency_key"], incident_id, report["version"],
                 report["report_id"], report["version"])).fetchall()
            if rows:
                require(len(rows) == 1 and rows[0]["snapshot"] == payload,
                        "An approval identity or version was reused with different content", "VERSION_CONFLICT")
                row = rows[0]
                created = False
            else:
                conflict = db.execute("SELECT 1 FROM knowledge WHERE (incident_id=? AND report_id<>?) OR (report_id=? AND incident_id<>?)",
                                      (incident_id, report["report_id"], report["report_id"], incident_id)).fetchone()
                require(conflict is None, "Report identity must remain stable for its incident", "VERSION_CONFLICT")
                db.execute("INSERT INTO knowledge VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (knowledge_id, incident_id, report["report_id"], report["version"],
                            decision_id, decision["idempotency_key"], payload))
                row = db.execute("SELECT * FROM knowledge WHERE knowledge_id=?", (knowledge_id,)).fetchone()
                created = True
            record = self._record(row, self._current_version(db, incident_id))
        return {"status": "stored", "created": created, "item": record}

    def get_approved_knowledge(self, knowledge_id):
        """Read any stored approved revision. Return None for an unknown ID."""
        text(knowledge_id, "knowledge_id")
        with self._connection() as db:
            row = db.execute("SELECT * FROM knowledge WHERE knowledge_id=?", (knowledge_id,)).fetchone()
            return None if row is None else self._record(row, self._current_version(db, row["incident_id"]))

    def list_approved_versions(self, incident_id):
        text(incident_id, "incident_id")
        with self._connection() as db:
            rows = db.execute("SELECT * FROM knowledge WHERE incident_id=? ORDER BY version DESC", (incident_id,)).fetchall()
            return [self._record(row, rows[0]["version"]) for row in rows]

    def search_approved_incidents(self, query="", affected_services=None, limit=5, exclude_incident_id=None,
                                  min_matched_terms=None):
        """Explainable matching, latest approved versions only. Empty != failed."""
        require(isinstance(query, str), "query must be a string")
        services = [] if affected_services is None else affected_services
        strings(services, "affected_services")
        integer(limit, "limit")
        require(limit <= 100, "limit must be <= 100")
        if exclude_incident_id is not None:
            text(exclude_incident_id, "exclude_incident_id")
        if min_matched_terms is not None:
            require(type(min_matched_terms) is int and min_matched_terms >= 0, "min_matched_terms must be >= 0")
        operation_id = str(uuid4())
        try:
            with self._connection() as db:
                rows = db.execute("""SELECT k.* FROM knowledge k WHERE version=(
                    SELECT MAX(version) FROM knowledge WHERE incident_id=k.incident_id)
                    ORDER BY incident_id""").fetchall()
            ranked = []
            for row in rows:
                if row["incident_id"] == exclude_incident_id:
                    continue
                record = self._record(row, row["version"])
                basis = self.scorer.score(query, services, record, min_matched_terms)
                if basis is None:
                    continue
                record["relevance_basis"] = basis
                ranked.append(record)
            ranked.sort(key=lambda r: (-r["relevance_basis"]["ranking_score"], r["source_incident_id"]))
            return {"status": "ok", "items": ranked[:limit], "error": None}
        except MemoryOperationError as exc:
            return {"status": "failed", "items": [], "error": exc.envelope(operation_id)}

    def check_memory(self, incident, findings=None, limit=5):
        """The diagram's memory read tool. Returns a search result for the caller's state.

        ``findings`` may be claim strings or F2 ``SignalFinding``-like objects; only their
        ``claim`` and ``indicator`` text is used for matching. Each item also gets a
        ``comparison`` of services, indicators and signals between the past and current incident.
        """
        require(isinstance(incident, dict), "incident must be an object")
        text(incident.get("incident_id"), "incident_id")
        text(incident.get("title"), "title")
        strings(incident.get("affected_services"), "affected_services")
        claims = [] if findings is None else findings
        require(isinstance(claims, list), "findings must be a list")
        parts = []
        for item in claims:
            if isinstance(item, dict):
                text(item.get("claim"), "finding.claim")
                parts.append(item["claim"])
                if item.get("indicator") is not None:
                    text(item["indicator"], "finding.indicator")
                    parts.append(item["indicator"])
            else:
                text(item, "findings")
                parts.append(item)
        result = self.search_approved_incidents(" ".join([incident["title"], *parts]),
            incident["affected_services"], limit, incident["incident_id"], CONTEXT_MIN_MATCHED_TERMS)
        for item in result["items"]:
            item["comparison"] = _comparison(item, claims, incident["affected_services"])
        return result
