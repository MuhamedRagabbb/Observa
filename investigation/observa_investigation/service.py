"""
Investigation Service and SQLite Ledger (F2-1, F2-2, F2-7).
Owns investigation run history, limit enforcement, idempotency, and audit trails.
Callers cannot bypass limits because counts are strictly computed from the ledger database.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .contracts import (
    ErrorEnvelope,
    F2Error,
    FeedbackIntent,
    FollowUpRequest,
    IncidentV1,
    InvestigationResult,
)
from .config import InvestigationConfig
from .orchestrator import Orchestrator, incident_id_of, validate_incident


class InvestigationLedger:
    """SQLite-backed audit ledger that durably tracks runs, limits, and review cycles."""

    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        # Every sqlite3.connect(":memory:") is a brand-new empty database, so an in-memory
        # ledger must keep ONE connection for its whole life.
        self._shared: Optional[sqlite3.Connection] = None
        if self.db_path == ":memory:":
            self._shared = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared.row_factory = sqlite3.Row
        self._init_db()

    @contextmanager
    def _get_conn(self):
        """Yield a connection inside a transaction (commit on success, rollback on error)."""
        if self._shared is not None:
            with self._lock, self._shared:
                yield self._shared
            return
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    parent_run_id TEXT,
                    origin TEXT NOT NULL,
                    request_id TEXT,
                    review_cycle_id TEXT,
                    status TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_incident_origin 
                ON runs(incident_id, origin)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_request_id 
                ON runs(request_id)
                """
            )

    def record_run(
        self,
        result: InvestigationResult,
        origin: str,
        request_id: Optional[str] = None,
        review_cycle_id: Optional[str] = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs 
                (run_id, incident_id, parent_run_id, origin, request_id, review_cycle_id, status, result_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.incident_id,
                    result.parent_run_id,
                    origin,
                    request_id,
                    review_cycle_id,
                    result.status,
                    result.model_dump_json(),
                    now,
                ),
            )

    def get_run(self, run_id: str) -> Optional[InvestigationResult]:
        with self._get_conn() as conn:
            cur = conn.execute("SELECT result_json FROM runs WHERE run_id = ?", (run_id,))
            row = cur.fetchone()
            if row:
                return InvestigationResult.model_validate_json(row["result_json"])
        return None

    def get_by_request_id(self, request_id: str) -> Optional[InvestigationResult]:
        if not request_id:
            return None
        with self._get_conn() as conn:
            cur = conn.execute("SELECT result_json FROM runs WHERE request_id = ?", (request_id,))
            row = cur.fetchone()
            if row:
                return InvestigationResult.model_validate_json(row["result_json"])
        return None

    def count_reinvestigations(self, incident_id: str) -> int:
        """Count how many re-investigations (correlation follow-ups) have run for this incident."""
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE incident_id = ? AND origin = 'correlation'",
                (incident_id,),
            )
            return cur.fetchone()[0]

    def count_review_cycles(self, incident_id: str) -> int:
        """Count how many human feedback review cycles have run for this incident."""
        with self._get_conn() as conn:
            cur = conn.execute(
                "SELECT COUNT(*) FROM runs WHERE incident_id = ? AND origin = 'human_feedback'",
                (incident_id,),
            )
            return cur.fetchone()[0]


class InvestigationService:
    """Public F2 API boundary.
    Owns limits, enforces contracts, manages idempotency, and drives the orchestrator.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        ledger: Optional[InvestigationLedger] = None,
        config: Optional[InvestigationConfig] = None,
    ):
        self.orchestrator = orchestrator
        self.config = config or orchestrator.config
        self.ledger = ledger or InvestigationLedger()

    def investigate(self, incident: Union[dict, IncidentV1]) -> InvestigationResult:
        """Run initial multi-signal investigation and record in ledger."""
        valid_inc = validate_incident(incident)
        result = self.orchestrator.investigate(valid_inc)
        self.ledger.record_run(result, origin="initial")
        return result

    def submit_follow_up(
        self,
        request: FollowUpRequest,
        incident: Union[dict, IncidentV1],
        prior_result: Optional[InvestigationResult] = None,
    ) -> InvestigationResult:
        """Process an F3 FollowUpRequest.
        Enforces idempotency and max_reinvestigations strictly via the ledger.
        """
        valid_inc = validate_incident(incident)
        iid = valid_inc.incident_id

        if request.incident_id != iid:
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message=f"FollowUpRequest incident_id {request.incident_id!r} does not match incident {iid!r}",
                    retryable=False,
                    operation_id=request.request_id,
                )
            )

        # Idempotency check: if already processed, return cached result
        cached = self.ledger.get_by_request_id(request.request_id)
        if cached is not None:
            return cached

        # Enforce origin-specific limit from ledger (caller count is ignored!)
        if request.origin == "human_feedback":
            current_cycles = self.ledger.count_review_cycles(iid)
            if current_cycles >= self.config.max_review_cycles:
                raise F2Error(
                    ErrorEnvelope(
                        code="RETRY_LIMIT_REACHED",
                        message=(
                            f"review cycle limit reached ({current_cycles} >= "
                            f"{self.config.max_review_cycles}) for incident {iid}"
                        ),
                        retryable=False,
                        operation_id=request.request_id,
                    )
                )
            result = self.orchestrator.follow_up(
                request=request,
                incident=valid_inc,
                prior_result=prior_result,
                review_cycle_count=current_cycles,
            )
            ledger_origin = "human_feedback"
        else:
            current_count = self.ledger.count_reinvestigations(iid)
            if current_count >= self.config.max_reinvestigations:
                raise F2Error(
                    ErrorEnvelope(
                        code="RETRY_LIMIT_REACHED",
                        message=(
                            f"re-investigation limit reached ({current_count} >= "
                            f"{self.config.max_reinvestigations}) for incident {iid}"
                        ),
                        retryable=False,
                        operation_id=request.request_id,
                    )
                )
            result = self.orchestrator.follow_up(
                request=request,
                incident=valid_inc,
                prior_result=prior_result,
                reinvestigation_count=current_count,
            )
            ledger_origin = "correlation"

        # Record in ledger
        self.ledger.record_run(
            result,
            origin=ledger_origin,
            request_id=request.request_id,
            review_cycle_id=request.review_cycle_id,
        )
        return result

    def submit_feedback(
        self,
        intent: FeedbackIntent,
        incident: Union[dict, IncidentV1],
        prior_result: Optional[InvestigationResult] = None,
    ) -> InvestigationResult:
        """Process an F6 FeedbackIntent.
        Enforces ambiguous feedback guardrail and max_review_cycles via the ledger.
        """
        valid_inc = validate_incident(incident)
        iid = valid_inc.incident_id

        if intent.incident_id != iid:
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message=f"FeedbackIntent incident_id {intent.incident_id!r} does not match incident {iid!r}",
                    retryable=False,
                    operation_id=intent.feedback_id,
                )
            )

        # Guardrail: ambiguous feedback without prior result must NOT run
        if intent.agent_status == "inconclusive":
            if prior_result:
                return prior_result
            raise F2Error(
                ErrorEnvelope(
                    code="VALIDATION_ERROR",
                    message="Ambiguous feedback with no prior result; auto-reinvestigation is prohibited",
                    retryable=False,
                    operation_id=intent.feedback_id,
                )
            )

        # Enforce review cycle limit from ledger
        current_cycles = self.ledger.count_review_cycles(iid)
        if current_cycles >= self.config.max_review_cycles:
            raise F2Error(
                ErrorEnvelope(
                    code="RETRY_LIMIT_REACHED",
                    message=(
                        f"review cycle limit reached ({current_cycles} >= "
                        f"{self.config.max_review_cycles}) for incident {iid}"
                    ),
                    retryable=False,
                    operation_id=intent.feedback_id,
                )
            )

        result = self.orchestrator.handle_feedback(
            intent=intent,
            incident=valid_inc,
            prior_result=prior_result,
            review_cycle_count=current_cycles,
        )

        self.ledger.record_run(
            result,
            origin="human_feedback",
            request_id=intent.feedback_id,
            review_cycle_id=intent.review_cycle_id,
        )
        return result
