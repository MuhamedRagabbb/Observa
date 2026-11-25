"""The outbox worker: approvals to F5 (memory), change requests to the feedback interpreter, and
interpreted requests to F2 for a targeted re-investigation.

    python -m observa_review_service.worker --once            # deliver everything due now, then exit
    python -m observa_review_service.worker --loop [--interval 2]

F5 and F2 are local mocks until those features ship: approved records are written as JSON files
(MEMORY_SINK_DIR) and F2 answers from the fixture bundles (a revised bundle for the incident
becomes vN+1 for review; none fails the revision visibly). Several workers may run at once:
claims are leased and acknowledgements are fenced.
"""
import argparse
import time
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import Engine, func, select

from observa_hitl.feedback import interpret_handler, reinvestigation_handler
from observa_hitl.interpreter import FakeInterpreter, FeedbackInterpreter
from observa_hitl.outbox import INTERPRET_EVENT, MEMORY_EVENT, REINVESTIGATION_EVENT, Handler, Relay, \
    memory_handler
from observa_hitl.ports import MemorySink, Reinvestigator
from observa_hitl.tables import outbox_events

from .db import make_engine, upgrade
from .mocks.memory_sink import FileMemorySink
from .mocks.reinvestigator import FixtureReinvestigator
from .seed import FIXTURES
from .settings import Settings

MAX_PASSES_PER_RUN = 20  # --once: one event can enqueue the next (interpret -> re-investigate)


def build_interpreter(settings: Settings) -> FeedbackInterpreter:
    """INTERPRETER=fake (deterministic, the default and what tests use) or groq (needs GROQ_API_KEY)."""
    if settings.INTERPRETER == "groq":
        from observa_hitl.interpreter.groq import GroqInterpreter
        return GroqInterpreter.from_settings(api_key=settings.GROQ_API_KEY or "", base_url=settings.GROQ_BASE_URL,
                                             model=settings.GROQ_MODEL)
    return FakeInterpreter()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def build_handlers(settings: Settings, engine: Engine, *, memory_sink: MemorySink | None = None,
                   interpreter: FeedbackInterpreter | None = None, reinvestigator: Reinvestigator | None = None,
                   clock: Callable[[], datetime] = _now) -> dict[str, Handler]:
    sink = memory_sink or FileMemorySink(settings.MEMORY_SINK_DIR)
    f2 = reinvestigator or FixtureReinvestigator(engine, FIXTURES, max_review_cycles=settings.MAX_REVIEW_CYCLES,
                                                 clock=clock)
    return {MEMORY_EVENT: memory_handler(sink),
            INTERPRET_EVENT: interpret_handler(engine, interpreter or build_interpreter(settings), clock=clock),
            REINVESTIGATION_EVENT: reinvestigation_handler(engine, f2, clock=clock)}


def build_relay(settings: Settings, engine: Engine, *, clock: Callable[[], datetime] = _now, **kw) -> Relay:
    return Relay(engine, build_handlers(settings, engine, clock=clock, **kw), max_attempts=settings.OUTBOX_MAX_ATTEMPTS,
                 lease_seconds=settings.OUTBOX_LEASE_SECONDS, clock=clock)


def _status_line(engine: Engine) -> str:
    with engine.connect() as conn:
        counts = dict(conn.execute(select(outbox_events.c.status, func.count()).group_by(outbox_events.c.status)).all())
    return ", ".join(f"{s} {counts.get(s, 0)}" for s in ("delivered", "pending", "failed"))


def drain(relay: Relay) -> int:
    """Run passes until nothing is due (bounded), so follow-on events are delivered in the same run."""
    total = 0
    for _ in range(MAX_PASSES_PER_RUN):
        processed = relay.run_once()
        total += processed
        if not processed:
            break
    return total


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="deliver everything due now and exit")
    mode.add_argument("--loop", action="store_true", help="keep running until interrupted")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between passes in --loop mode")
    ap.add_argument("--database-url", default=None)
    ap.add_argument("--memory-dir", default=None, help="where the F5 mock writes approved records")
    args = ap.parse_args(argv)
    overrides = {k: v for k, v in (("DATABASE_URL", args.database_url), ("MEMORY_SINK_DIR", args.memory_dir)) if v}
    settings = Settings(**overrides)
    upgrade(settings.DATABASE_URL)
    engine = make_engine(settings.DATABASE_URL)
    relay = build_relay(settings, engine)
    try:
        while True:
            processed = drain(relay)
            if args.once or processed:
                print(f"processed {processed}; outbox: {_status_line(engine)}", flush=True)
            if args.once:
                return
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
