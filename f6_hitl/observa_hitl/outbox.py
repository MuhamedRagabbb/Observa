"""Transactional outbox and its relay (F6-US-01 crash-safe handoff).

Events are written in the same transaction as the decision that causes them; `enqueue` is
idempotent on (event_type, dedupe_key). The relay delivers them at least once:

1. claim: a short transaction sets a lease (`claimed_until`) and counts the attempt;
2. deliver: the handler runs OUTSIDE any transaction (it may call F5, Groq or F2);
3. record: a second transaction marks the event delivered, fenced by the attempt number so a
   worker whose lease was reclaimed cannot acknowledge, or the failure is recorded with
   exponential backoff, `min(300, 5 * 2**attempts)` seconds, and terminal `failed` at
   `max_attempts`.

A handler takes the event payload and may return a database step `(conn) -> None`; that step
commits in the same transaction as the acknowledgement, so its effects happen exactly once.
Receivers must be idempotent (F5 on decision_id), because a crash between delivery and
acknowledgement redelivers.
"""
import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import Connection, Engine, and_, or_, select, update

from .contracts import event_id_for, iso
from .errors import F6Error
from .ports import MEMORY_EVENT, ApprovedReportHandoff, MemorySink
from .tables import outbox_events

INTERPRET_EVENT = "feedback.interpret.v1"
REINVESTIGATION_EVENT = "reinvestigation.request.v1"
EVENT_TYPES = (MEMORY_EVENT, INTERPRET_EVENT, REINVESTIGATION_EVENT)

DbStep = Callable[[Connection], None]
Handler = Callable[[dict], DbStep | None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def backoff_seconds(attempts: int) -> int:
    return min(300, 5 * 2 ** attempts)


def _insert_ignore(conn: Connection, table, values: dict) -> None:
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    conn.execute(insert(table).values(**values).on_conflict_do_nothing())


class Outbox:
    def __init__(self, clock: Callable[[], datetime] = _now):
        self._clock = clock

    def enqueue(self, conn: Connection, event_type: str, dedupe_key: str, payload: dict) -> str:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"unknown outbox event type {event_type!r}")
        event_id = event_id_for(event_type, dedupe_key)
        now = iso(self._clock())
        _insert_ignore(conn, outbox_events, {
            "event_id": event_id, "event_type": event_type, "dedupe_key": dedupe_key,
            "payload": json.dumps(payload, sort_keys=True, separators=(",", ":")), "status": "pending",
            "attempts": 0, "next_attempt_at": now, "claimed_until": None, "created_at": now})
        return event_id

    def retry(self, conn: Connection, event_id: str) -> None:
        """Requeue a `failed` event with a fresh attempt budget (an operator action)."""
        op = f"outbox.retry:{event_id}"
        status = conn.execute(select(outbox_events.c.status).where(outbox_events.c.event_id == event_id))\
            .scalar_one_or_none()
        if status is None:
            raise F6Error("NOT_FOUND", f"Outbox event {event_id} does not exist.", operation_id=op)
        changed = conn.execute(update(outbox_events).where(
            outbox_events.c.event_id == event_id, outbox_events.c.status == "failed",
        ).values(status="pending", attempts=0, next_attempt_at=iso(self._clock()), claimed_until=None))
        if changed.rowcount != 1:
            raise F6Error("VERSION_CONFLICT", f"Only a failed event can be retried; this one is {status}.",
                          operation_id=op, details={"status": status})


class _StaleClaim(Exception):
    """Our lease was reclaimed by another worker; our outcome must not be recorded."""


class Relay:
    def __init__(self, engine: Engine, handlers: dict[str, Handler], *, max_attempts: int = 8,
                 clock: Callable[[], datetime] = _now, lease_seconds: int = 60, batch: int = 20):
        unknown = set(handlers) - set(EVENT_TYPES)
        if unknown:
            raise ValueError(f"handlers for unknown event types: {sorted(unknown)}")
        self._engine, self._handlers = engine, dict(handlers)
        self._max_attempts, self._clock = max_attempts, clock
        self._lease, self._batch = timedelta(seconds=lease_seconds), batch

    def run_once(self) -> int:
        """Claim due events this relay has handlers for and try each once. Returns how many it tried."""
        claimed = self._claim()
        for event in claimed:
            self._deliver(event)
        return len(claimed)

    def _claim(self) -> list[dict]:
        now = self._clock()
        due = iso(now)
        with self._engine.begin() as conn:
            rows = conn.execute(select(outbox_events).where(
                outbox_events.c.status == "pending",
                outbox_events.c.event_type.in_(self._handlers),
                outbox_events.c.next_attempt_at <= due,
                or_(outbox_events.c.claimed_until.is_(None), outbox_events.c.claimed_until <= due),
            ).order_by(outbox_events.c.next_attempt_at, outbox_events.c.event_id).limit(self._batch)).all()
            claimed = []
            for row in rows:
                attempt = row.attempts + 1
                got = conn.execute(update(outbox_events).where(
                    outbox_events.c.event_id == row.event_id, outbox_events.c.status == "pending",
                    outbox_events.c.attempts == row.attempts,
                ).values(attempts=attempt, claimed_until=iso(now + self._lease)))
                if got.rowcount == 1:
                    claimed.append({"event_id": row.event_id, "event_type": row.event_type,
                                    "payload": json.loads(row.payload), "attempt": attempt})
        return claimed

    def _mine(self, event: dict):
        return and_(outbox_events.c.event_id == event["event_id"], outbox_events.c.status == "pending",
                    outbox_events.c.attempts == event["attempt"])

    def _deliver(self, event: dict) -> None:
        try:
            step = self._handlers[event["event_type"]](event["payload"])
            with self._engine.begin() as conn:
                acked = conn.execute(update(outbox_events).where(self._mine(event)).values(
                    status="delivered", delivered_at=iso(self._clock()), claimed_until=None, last_error=None))
                if acked.rowcount != 1:
                    raise _StaleClaim()  # another worker owns the event now: do nothing at all
                if step is not None:
                    step(conn)  # fails -> the acknowledgement rolls back with it
        except _StaleClaim:
            return
        except Exception as exc:  # noqa: BLE001 - every delivery failure is recorded and retried
            self._record_failure(event, f"{type(exc).__name__}: {exc}"[:1000])

    def _record_failure(self, event: dict, error: str) -> None:
        attempt = event["attempt"]
        terminal = attempt >= self._max_attempts
        values = {"last_error": error, "claimed_until": None}
        if terminal:
            values["status"] = "failed"
        else:
            values["next_attempt_at"] = iso(self._clock() + timedelta(seconds=backoff_seconds(attempt)))
        with self._engine.begin() as conn:
            conn.execute(update(outbox_events).where(self._mine(event)).values(**values))


def memory_handler(sink: MemorySink) -> Handler:
    """memory.approved_report.v1 -> F5. The sink is idempotent on decision_id."""
    def handle(payload: dict) -> None:
        sink.store(ApprovedReportHandoff.model_validate(payload))
    return handle
