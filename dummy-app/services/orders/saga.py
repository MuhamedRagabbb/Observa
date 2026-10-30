"""Multi-item checkout saga (Orders): reserve → price → persist → charge → settle → complete.

Each step is persisted through the AttemptStore before the next remote call, and every remote
call is idempotent per order_id, so a persisted attempt can be re-driven after a crash or a lost
response. Rules:

- An unknown reservation outcome is settled by releasing (the inventory tombstone fences a late
  reserve) before the attempt fails.
- An unknown payment outcome (503, timeout, lost response) is settled by a void fence; stock is
  released only after the payment is confirmed declined or voided, never while it is unknown.
- 402 from Payments is a definitive decline.
- A replay or resolve re-drives an attempt only after ``resume_after`` seconds without progress,
  so it never races a request that is still running. A replay re-runs the stalled step (every
  call is idempotent); an explicit resolve instead *settles* it: an unconfirmed reservation is
  released and an unconfirmed charge is voided, so resolving never takes new money.
- The background reconciler (``reconciler.py``) settles attempts nobody resumes, via ``settle``.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from opentelemetry.trace import Status, StatusCode

from services.shared.checkout import TERMINAL_STATES
from services.shared.errors import AppError
from services.shared.telemetry import current_traceparent

VOID_EVENTS = {"voided": "voided", "succeeded": "found_succeeded", "declined": "found_declined"}
# Explicit resolution of an in-flight step: fence it instead of retrying it.
SETTLE_EVENTS = {
    "received": "expire",
    "reserving": "reserve_unknown",
    "reserved": "persist_failed",
    "charging": "charge_unknown",
}


class CheckoutSaga:
    def __init__(self, attempts, client, settings, telemetry, runtime, *, resume_after=None):
        self.attempts, self.client, self.settings = attempts, client, settings
        self.tel, self.runtime = telemetry, runtime
        # Longer than the slowest single step (two dependency attempts plus the F3 delay).
        self.resume_after = (
            max(15, 2 * settings.dependency_timeout + 5) if resume_after is None else resume_after
        )
        self.steps = {
            "received": self._received,
            "reserving": self._reserving,
            "reserved": self._reserved,
            "charging": self._charging,
            "payment_unknown": self._payment_unknown,
            "completing": self._completing,
            "releasing": self._releasing,
            "releasing_declined": self._releasing,
            "releasing_voided": self._releasing,
        }

    async def place(self, attempt_ref, order):
        """Start (or idempotently replay) a checkout and drive it as far as it can safely go."""
        view = await self.attempts.begin(attempt_ref, order, trace_parent=current_traceparent())
        if view["replayed"]:
            return await self._resume(view)
        self.tel.count("orders.created")
        self.tel.log("INFO", "order_created", "Order created", order_id=view["order_id"])
        return await self._drive(view)

    async def resolve(self, customer_id, attempt_ref):
        """Explicit resolution: fence an absent attempt, or settle a stalled one."""
        return await self._resume(await self.attempts.abandon(customer_id, attempt_ref), settle=True)

    async def settle(self, view):
        """Settle a stalled attempt the caller has already claimed: fence the in-flight step
        (release an unconfirmed reservation, void an unconfirmed charge) and finish what is
        already decided. Never takes new money; stops early while an outcome stays unknown."""
        if view["state"] in TERMINAL_STATES:
            return view
        return await self._drive(view, settle=True)

    async def _resume(self, view, *, settle=False):
        if view["state"] in TERMINAL_STATES:
            return view
        if datetime.now(UTC) - view["updated_at"] < timedelta(seconds=self.resume_after):
            return view  # another request may still be driving it; report processing
        self.tel.log("INFO", "checkout_resumed", "Resuming checkout", order_id=view["order_id"])
        return await self._drive(view, settle=settle)

    async def _drive(self, view, *, settle=False):
        attributes = {"workflow.stage": "checkout", **self.runtime.attributes()}
        with self.tel.tracer.start_as_current_span("order.workflow", attributes=attributes):
            if settle and view["state"] in SETTLE_EVENTS:
                try:
                    view = await self._advance(
                        view, SETTLE_EVENTS[view["state"]], error_code="checkout_unconfirmed"
                    )
                except AppError as exc:
                    if exc.code == "stale_attempt":
                        return await self.attempts.get(view["customer_id"], view["attempt_ref"])
                    raise
            while view["state"] not in TERMINAL_STATES:
                try:
                    progressed = await self.steps[view["state"]](view)
                except AppError as exc:
                    if exc.code == "stale_attempt":
                        return await self.attempts.get(view["customer_id"], view["attempt_ref"])
                    raise
                if progressed is None:
                    return view  # blocked on an unconfirmed dependency; safe to resume later
                view = progressed
            self._finished(view)
            return view

    def _finished(self, view):
        if view["state"] == "completed":
            self.tel.count("orders.completed")
            self.tel.log("INFO", "order_completed", "Order completed", order_id=view["order_id"])
        else:
            self.tel.count("orders.failed")
            self.tel.log(
                "ERROR",
                "order_failed",
                "Order workflow failed",
                order_id=view["order_id"],
                **{"error.type": view["error_code"] or view["state"]},
            )

    def _advance(self, view, event, **fields):
        return self.attempts.advance(
            view["customer_id"], view["attempt_ref"], event, expected_version=view["version"], **fields
        )

    def _call(self, dependency, base_url, method, route, **kwargs):
        return self.client.request(dependency, base_url, method, route, retry=True, **kwargs)

    async def _received(self, view):
        return await self._advance(view, "reserve")

    async def _reserving(self, view):
        items = [{"product_id": line["product_id"], "quantity": line["quantity"]} for line in view["items"]]
        try:
            await self._call(
                "inventory-service",
                self.settings.inventory_url,
                "POST",
                "/inventory/reservations",
                body={"order_id": view["order_id"], "items": items},
            )
        except AppError as exc:
            if exc.code in ("insufficient_stock", "product_not_found"):
                requested = {line["product_id"]: line["quantity"] for line in view["items"]}
                unavailable = (exc.details or {}).get("unavailable_items") or [
                    {"product_id": product, "requested": requested.get(product), "available": 0}
                    for product in (exc.details or {}).get("unknown_products", [])
                ]
                return await self._advance(
                    view, "out_of_stock", error_code=exc.code, details={"unavailable_items": unavailable}
                )
            # Unconfirmed: the release tombstone will fence a reservation that is still in flight.
            return await self._advance(view, "reserve_unknown", error_code=exc.code)
        return await self._advance(view, "reserved")

    async def _reserved(self, view):
        try:
            products = await self._call(
                "inventory-service", self.settings.inventory_url, "GET", "/inventory/products"
            )
        except AppError:
            return None  # prices unavailable: stock stays reserved until the attempt is resumed
        with self.tel.tracer.start_as_current_span(
            "order.persistence", attributes={"workflow.stage": "persist", **self.runtime.attributes()}
        ) as span:
            if await self._buggy_release_fails(view):
                span.record_exception(RuntimeError("OrderPersistenceCompatibilityError"))
                span.set_status(Status(StatusCode.ERROR, "OrderPersistenceCompatibilityError"))
                self.tel.log(
                    "ERROR",
                    "order_persistence_failed",
                    "Order persistence compatibility check failed",
                    order_id=view["order_id"],
                    **{"error.type": "OrderPersistenceCompatibilityError", **self.runtime.attributes()},
                )
                return await self._advance(view, "persist_failed", error_code="order_persistence_error")
            prices = {product["product_id"]: product["price_cents"] for product in products}
            if any(line["product_id"] not in prices for line in view["items"]):
                # No payment has started, so releasing the reservation is safe and final.
                return await self._advance(view, "persist_failed", error_code="price_unavailable")
            return await self._advance(view, "charge", prices=prices)

    async def _buggy_release_fails(self, view):
        """Same F3 regression as the legacy flow: slow persistence, failing for an even unit count."""
        policy = self.runtime.settings
        if not (policy and policy.release_version == "v2-buggy"):
            return False
        try:
            await asyncio.wait_for(self.runtime.released.wait(), policy.persistence_delay_seconds)
            return False  # rolled back while waiting
        except TimeoutError:
            return sum(line["quantity"] for line in view["items"]) % 2 == 0

    async def _charging(self, view):
        try:
            await self._call(
                "payments-service",
                self.settings.payments_url,
                "POST",
                "/payments/charge",
                body={
                    "order_id": view["order_id"],
                    "idempotency_key": view["order_id"],
                    "customer_id": view["customer_id"],
                    "amount_cents": view["total_cents"],
                },
            )
        except AppError as exc:
            if exc.status == 402:
                return await self._advance(view, "declined", error_code=exc.code)
            # 503, timeout, lost response, or an already-voided key: never proof of "not charged".
            self.tel.log(
                "WARN",
                "payment_outcome_unknown",
                "Payment outcome not confirmed",
                order_id=view["order_id"],
                **{"error.type": exc.code},
            )
            return await self._advance(view, "charge_unknown", error_code=exc.code)
        return await self._advance(view, "charged")

    async def _payment_unknown(self, view):
        try:
            result = await self._call(
                "payments-service",
                self.settings.payments_url,
                "POST",
                f"/payments/{view['order_id']}/void",
                template="/payments/{idempotency_key}/void",
                body={"order_id": view["order_id"]},
            )
        except AppError as exc:
            self.tel.log(
                "ERROR",
                "payment_void_failed",
                "Payment outcome still unconfirmed",
                order_id=view["order_id"],
                **{"error.type": exc.code},
            )
            return None
        event = VOID_EVENTS.get(result.get("status"))
        if event is None:
            self.tel.log("ERROR", "payment_void_failed", "Unexpected void result", order_id=view["order_id"])
            return None  # still unknown: keep stock and wait for a later resolve
        return await self._advance(view, event)

    async def _completing(self, view):
        return await self._advance(view, "persisted")

    async def _releasing(self, view):
        try:
            await self._call(
                "inventory-service",
                self.settings.inventory_url,
                "POST",
                f"/inventory/reservations/{view['order_id']}/release",
                template="/inventory/reservations/{reservation_id}/release",
            )
        except AppError:
            self.tel.log(
                "ERROR",
                "inventory_release_failed",
                "Reservation cleanup requires attention",
                order_id=view["order_id"],
            )
            return None
        return await self._advance(view, "released")
