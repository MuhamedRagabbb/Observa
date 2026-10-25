"""Background reconciler (Orders): settles checkout attempts that nobody is driving any more.

Each cycle claims stalled attempts one at a time (a short leased claim, no transaction held
during remote calls) and settles them through ``CheckoutSaga.settle``: it never takes new
money, never guesses an outcome and never releases stock while a payment is unconfirmed.
Correctness rests on the saga's compare-and-set steps and the void/release fences; the lease
only keeps concurrent reconcilers from duplicating work. Under database pool pressure (F1) a
cycle ends at once rather than adding load.
"""

import asyncio
from dataclasses import dataclass

from opentelemetry.context import Context
from opentelemetry.metrics import Observation

from services.shared.checkout import TERMINAL_STATES
from services.shared.errors import AppError
from services.shared.telemetry import link_to

POOL_PRESSURE = frozenset({"db_pool_timeout", "database_unavailable"})


@dataclass
class CycleReport:
    claimed: int = 0  # attempts this cycle took a lease on
    settled: int = 0  # of those, how many reached a final state
    stuck: int = 0  # of those, how many just crossed the stuck threshold
    skipped: str | None = None  # why the cycle ended early, if it did


class Reconciler:
    def __init__(
        self,
        attempts,
        saga,
        telemetry,
        *,
        policy,
        batch_size=20,
        stuck_after=10,
        clock=lambda: None,
    ):
        """``clock`` returns the time claims are judged at; None (the default) means the
        database's own clock, so every reconciler process agrees on stalls and leases."""
        self.attempts, self.saga, self.tel, self.policy = attempts, saga, telemetry, policy
        self.batch_size, self.stuck_after, self.clock = batch_size, stuck_after, clock
        self.stuck_by_state = {}
        telemetry.meter.create_observable_gauge(
            "orders.reconciliation.stuck",
            [lambda _: [Observation(n, {"state": state}) for state, n in self.stuck_by_state.items()]],
        )

    def start(self, interval):
        """Run cycles in the background: the first one at once (resuming work left by a crash
        or restart), then one every ``interval`` seconds."""
        self.task = asyncio.create_task(self._loop(interval))

    async def stop(self):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass

    async def _loop(self, interval):
        while True:
            try:
                await self.run_cycle()
            except Exception as exc:  # keep the loop alive; the next cycle retries
                self.tel.log(
                    "ERROR",
                    "reconciliation_cycle_failed",
                    "Reconciliation cycle failed",
                    **{"error.type": type(exc).__name__},
                )
            await asyncio.sleep(interval)

    async def run_cycle(self):
        report = CycleReport()
        try:
            for _ in range(self.batch_size):
                claimed = await self.attempts.claim_stalled(self.policy, now=self.clock())
                if claimed is None:
                    break
                report.claimed += 1
                view = claimed
                try:
                    view = await self._settle(claimed)
                finally:
                    # Also when settling failed: the threshold check is only counted once.
                    await self._release(claimed)
                    if view["state"] in TERMINAL_STATES:
                        report.settled += 1
                    elif claimed["check_count"] == self.stuck_after:
                        report.stuck += 1
                        self._report_stuck(view)
            self.stuck_by_state = await self.attempts.count_stuck(self.stuck_after)
        except AppError as exc:
            if exc.code not in POOL_PRESSURE:
                raise
            report.skipped = exc.code
        return report

    def _report_stuck(self, view):
        self.tel.log(
            "WARN",
            "reconciliation_stuck",
            "Checkout still unresolved after repeated checks",
            order_id=view["order_id"],
            state=view["state"],
        )

    async def _settle(self, claimed):
        # Its own trace (a fresh root), linked to the checkout trace that started the attempt.
        with self.tel.tracer.start_as_current_span(
            "checkout.reconcile",
            context=Context(),
            links=link_to(claimed.get("trace_parent")),
            attributes={"workflow.stage": "reconcile", "checkout.state": claimed["state"]},
        ):
            return self._record_outcome(await self.saga.settle(claimed))

    def _record_outcome(self, view):
        if view["state"] in TERMINAL_STATES:
            self.tel.count("orders.reconciliation.settled", {"state": view["state"]})
            self.tel.log(
                "INFO",
                "reconciliation_completed",
                "Stalled checkout settled",
                order_id=view["order_id"],
                state=view["state"],
            )
        return view

    async def _release(self, claimed):
        try:
            await self.attempts.release_claim(claimed)
        except AppError:
            pass  # the lease simply expires
