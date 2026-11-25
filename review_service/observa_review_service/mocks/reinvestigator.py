"""F2 (Investigation) mocks behind F6's Reinvestigator port. Both are idempotent on request_id."""
import json
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import Engine, select

from observa_hitl.errors import F6Error
from observa_hitl.feedback import handle_revision_failed, handle_revision_ready
from observa_hitl.gate import ReviewGate
from observa_investigation.contracts import FeedbackIntent, FollowUpRequest
from observa_reports.errors import ReportError
from observa_reports.inputs import ReportInputs
from observa_reports.service import ReportService, report_id_for
from observa_reports.tables import report_versions

log = logging.getLogger(__name__)


class F2Outage(RuntimeError):
    """F2 is unreachable (injected)."""


class FakeReinvestigator:
    """Records requests, for tests.

    fail_times:          the first N calls fail before F2 accepts anything
    crash_after_request: the first call is accepted by F2 and then fails, as if the worker died
                         before acknowledging the outbox event
    """

    def __init__(self, *, fail_times: int = 0, crash_after_request: bool = False):
        self.fail_times, self.crash_after_request = fail_times, crash_after_request
        self.requests: dict[str, tuple[FollowUpRequest, FeedbackIntent]] = {}
        self.calls = 0

    def request(self, follow_up: FollowUpRequest, intent: FeedbackIntent) -> None:
        self.calls += 1
        if self.fail_times > 0:
            self.fail_times -= 1
            raise F2Outage("injected F2 outage")
        self.requests.setdefault(follow_up.request_id, (follow_up, intent))
        if self.crash_after_request:
            self.crash_after_request = False
            raise F2Outage("injected crash after F2 accepted the request")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FixtureReinvestigator:
    """A stand-in F2 that answers at once from the fixture bundles, so the whole loop runs locally.

    A bundle for the same incident whose F2 run has not been published yet is the "revised"
    investigation: it becomes vN+1 through the revision intake. With no such bundle the
    re-investigation fails visibly (revision_failed) and vN stays current.
    """

    def __init__(self, engine: Engine, bundles: Path, *, max_review_cycles: int = 3,
                 clock: Callable[[], datetime] = _now):
        self._engine, self._bundles, self._clock = engine, Path(bundles), clock
        self._gate = ReviewGate(clock=clock, max_review_cycles=max_review_cycles)
        self._reports = ReportService(clock=clock)

    def _revised(self, incident_id: str) -> ReportInputs | None:
        report_id = report_id_for(incident_id)
        with self._engine.connect() as conn:
            published = set(conn.execute(select(report_versions.c.source_investigation_run_id).where(
                report_versions.c.report_id == report_id)).scalars())
        for path in sorted(self._bundles.glob("*.json")):
            inputs = ReportInputs.model_validate(json.loads(path.read_text(encoding="utf-8")))
            if inputs.incident.incident_id == incident_id and inputs.investigation is not None \
                    and inputs.investigation.run_id not in published:
                return inputs
        return None

    def request(self, follow_up: FollowUpRequest, intent: FeedbackIntent) -> None:
        revised = self._revised(follow_up.incident_id)
        try:
            with self._engine.begin() as conn:
                if revised is not None:
                    handle_revision_ready(conn, intent.report_id, revised, follow_up.review_cycle_id,
                                          gate=self._gate, reports=self._reports, clock=self._clock)
                else:
                    handle_revision_failed(conn, follow_up.request_id,
                                           f"mock F2 has no revised fixture for {follow_up.incident_id}",
                                           clock=self._clock)
        except (F6Error, ReportError) as exc:
            if exc.envelope.retryable:  # e.g. STORAGE_FAILURE: let the outbox retry
                raise
            # A conflict: the cycle moved on (superseded, approved), so there is nothing left to answer.
            log.info("mock F2 answer for %s not applied: %s", follow_up.request_id, exc)
