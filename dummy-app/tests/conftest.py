import asyncio
import copy
import io
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from services.gateway.app import create_app as gateway_app
from services.inventory.app import create_app as inventory_app
from services.orders.app import create_app as orders_app
from services.payments.app import create_app as payments_app
from services.shared.attempts import LIST_DEFAULT, check_customer, check_key
from services.shared.checkout import TERMINAL_STATES, InvalidTransition
from services.shared.checkout import advance as next_state
from services.shared.client import DependencyClient
from services.shared.config import Settings
from services.shared.errors import AppError
from services.shared.telemetry import Telemetry


class MemoryDatabase:
    def __init__(self, runtime, tel):
        self.runtime, self.tel = runtime, tel

    async def open(self):
        pass

    async def close(self):
        pass

    async def ready(self):
        pass

    @asynccontextmanager
    async def operation(self, name):
        with self.tel.tracer.start_as_current_span("db." + name):
            yield


class MemoryInventory:
    def __init__(self, db):
        self.db = db
        self.stock = {"sku-001": 1000, "sku-002": 1000, "sku-004": 0}
        self.reservations = {}
        self.released = set()
        self.lock = asyncio.Lock()

    async def products(self):
        async with self.db.operation("products.select"):
            return [
                {"product_id": key, "name": key, "stock": stock, "price_cents": 1200}
                for key, stock in self.stock.items()
            ]

    async def reserve(self, data):
        async with self.lock, self.db.operation("inventory.reserve"):
            if data.order_id in self.released:
                raise AppError(409, "reservation_released", "Reservation key was already released")
            if data.order_id in self.reservations:
                old = self.reservations[data.order_id]
                if old.get("product_id") != data.product_id or old.get("quantity") != data.quantity:
                    raise AppError(409, "idempotency_conflict", "Conflict")
                return {"reservation_id": data.order_id, "status": old["status"]}
            if data.product_id not in self.stock:
                raise AppError(404, "product_not_found", "Unknown product")
            if self.stock[data.product_id] < data.quantity:
                raise AppError(409, "insufficient_stock", "Insufficient stock")
            self.stock[data.product_id] -= data.quantity
            self.reservations[data.order_id] = {**data.model_dump(), "status": "reserved"}
            return {"reservation_id": data.order_id, "status": "reserved"}

    async def reserve_lines(self, data):
        async with self.lock, self.db.operation("inventory.reserve"):
            key, lines = data.order_id, [(i.product_id, i.quantity) for i in data.items]
            if key in self.released:
                raise AppError(409, "reservation_released", "Reservation key was already released")
            old = self.reservations.get(key)
            if old:
                if old.get("lines") != lines:
                    raise AppError(409, "idempotency_conflict", "Conflict")
                return {"reservation_id": key, "status": old["status"]}
            unknown = [product for product, _ in lines if product not in self.stock]
            if unknown:
                raise AppError(404, "product_not_found", "Unknown product", {"unknown_products": unknown})
            short = [
                {"product_id": p, "requested": q, "available": self.stock[p]}
                for p, q in lines
                if self.stock[p] < q
            ]
            if short:
                raise AppError(409, "insufficient_stock", "Insufficient stock", {"unavailable_items": short})
            for product, quantity in lines:
                self.stock[product] -= quantity
            self.reservations[key] = {"lines": lines, "status": "reserved"}
            return {"reservation_id": key, "status": "reserved"}

    async def release(self, reservation_id):
        async with self.lock, self.db.operation("inventory.release"):
            self.released.add(reservation_id)
            old = self.reservations.get(reservation_id)
            if old and old["status"] == "reserved":
                for product, quantity in old.get("lines") or [(old["product_id"], old["quantity"])]:
                    self.stock[product] += quantity
                old["status"] = "released"
            return {"reservation_id": reservation_id, "status": "released"}


class MemoryOrders:
    def __init__(self, db):
        self.db, self.rows = db, {}

    async def create(self, order_id, data):
        async with self.db.operation("order.insert"):
            await self.db.runtime.hold()
            self.rows[order_id] = {
                "order_id": order_id,
                "product_id": data.product_id,
                "quantity": data.quantity,
                "status": "pending",
                "reservation_id": None,
                "payment_id": None,
                "error_code": None,
            }

    async def update(self, order_id, status, reservation_id=None, payment_id=None, error_code=None):
        async with self.db.operation("order.persist"):
            self.rows[order_id].update(
                status=status,
                reservation_id=reservation_id or self.rows[order_id]["reservation_id"],
                payment_id=payment_id,
                error_code=error_code,
            )

    async def get(self, order_id):
        async with self.db.operation("order.select"):
            if order_id not in self.rows:
                raise AppError(404, "order_not_found", "Unknown order")
            return self.rows[order_id].copy()


class MemoryPayments:
    def __init__(self, db):
        self.db, self.rows = db, {}

    async def lookup(self, key):
        old = self.rows.get(key)
        if old:
            return {"payment_id": old[1]["payment_id"], "order_id": old[2], "status": old[1]["status"]}

    async def void(self, key, order_id):
        async with self.db.operation("payment.void"):
            old = self.rows.setdefault(
                key, (None, {"payment_id": "void-" + order_id, "status": "voided"}, order_id)
            )
            if old[2] != order_id:
                raise AppError(409, "idempotency_conflict", "Conflict")
            return old[1]

    async def existing(self, data):
        old = self.rows.get(data.idempotency_key)
        if old:
            if old[1]["status"] == "voided":
                raise AppError(409, "payment_voided", "Voided")
            if old[0] != data:
                raise AppError(409, "idempotency_conflict", "Conflict")
            return old[1]

    async def charge(self, data, decline):
        async with self.db.operation("payment.insert"):
            result = {
                "payment_id": "payment-" + data.order_id,
                "status": "declined" if decline else "succeeded",
            }
            self.rows.setdefault(data.idempotency_key, (data, result, data.order_id))
            return await self.existing(data)


class MemoryAttempts:
    """In-memory AttemptStore with the same contract (begin/advance/abandon/get)."""

    def __init__(self, db):
        self.db, self.rows, self.lock, self.sequence = db, {}, asyncio.Lock(), 1000

    def _view(self, row, replayed=False):
        hidden = ("request_hash", "lines", "next_check_at", "lease_until", "check_count", "trace_parent")
        view = {key: value for key, value in row.items() if key not in hidden}
        items = [{k: v for k, v in line.items() if v is not None} for line in row["lines"]]
        return {**copy.deepcopy(view), "items": copy.deepcopy(items), "replayed": replayed}

    def _new_row(self, customer_id, attempt_ref, **fields):
        row = {
            "customer_id": customer_id,
            "attempt_ref": attempt_ref,
            "request_hash": None,
            "order_id": None,
            "order_number": None,
            "state": "received",
            "version": 0,
            "error_code": None,
            "total_cents": None,
            "details": None,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "lines": [],
            "next_check_at": None,
            "lease_until": None,
            "check_count": 0,
            "trace_parent": None,
        }
        row.update(fields)
        return row

    async def begin(self, attempt_ref, data, *, trace_parent=None):
        check_key(data.customer_id, attempt_ref)
        async with self.db.operation("attempt.begin"):
            await self.db.runtime.hold()
            async with self.lock:
                key = (data.customer_id, attempt_ref)
                if key in self.rows:
                    row = self.rows[key]
                    if row["state"] == "abandoned":
                        raise AppError(409, "attempt_abandoned", "Abandoned")
                    if row["request_hash"] != data.request_hash():
                        raise AppError(409, "idempotency_conflict", "Conflict")
                    return self._view(row, replayed=True)
                order_id = f"order-{uuid4()}"
                self.sequence += 1
                self.rows[key] = self._new_row(
                    data.customer_id,
                    attempt_ref,
                    request_hash=data.request_hash(),
                    order_id=order_id,
                    order_number=f"OBS-{self.sequence}-{order_id[6:10].upper()}",
                    trace_parent=trace_parent,
                    lines=[
                        {"product_id": i.product_id, "quantity": i.quantity, "unit_price_cents": None}
                        for i in data.items
                    ],
                )
                return self._view(self.rows[key])

    async def advance(
        self, customer_id, attempt_ref, event, *, expected_version, error_code=None, prices=None, details=None
    ):
        check_key(customer_id, attempt_ref)
        async with self.db.operation("attempt.advance"), self.lock:
            row = self.rows.get((customer_id, attempt_ref))
            if row is None:
                raise AppError(404, "attempt_not_found", "Missing")
            if row["version"] != expected_version:
                raise AppError(409, "stale_attempt", "Stale")
            try:
                state = next_state(row["state"], event)
            except InvalidTransition:
                raise AppError(409, "invalid_transition", "Invalid") from None
            if prices is not None:
                if any(line["product_id"] not in prices for line in row["lines"]):
                    raise AppError(502, "price_unavailable", "Missing price")
                for line in row["lines"]:
                    line["unit_price_cents"] = prices[line["product_id"]]
                row["total_cents"] = sum(line["unit_price_cents"] * line["quantity"] for line in row["lines"])
            row.update(state=state, version=row["version"] + 1, updated_at=datetime.now(UTC))
            row["error_code"] = error_code or row["error_code"]
            row["details"] = details if details is not None else row["details"]
            return self._view(row)

    async def abandon(self, customer_id, attempt_ref):
        check_key(customer_id, attempt_ref)
        async with self.db.operation("attempt.abandon"), self.lock:
            row = self.rows.setdefault(
                (customer_id, attempt_ref),
                self._new_row(customer_id, attempt_ref, state=next_state(None, "abandon")),
            )
            return self._view(row)

    async def claims_supported(self):
        return True

    async def claim_stalled(self, policy, *, now=None):
        now = now or datetime.now(UTC)
        async with self.db.operation("attempt.claim"), self.lock:
            due = [
                row
                for row in self.rows.values()
                if row["state"] not in TERMINAL_STATES
                and row["updated_at"] <= now - timedelta(seconds=policy.stall_seconds)
                and (row["next_check_at"] is None or row["next_check_at"] <= now)
                and (row["lease_until"] is None or row["lease_until"] <= now)
            ]
            if not due:
                return None
            row = min(due, key=lambda r: r["next_check_at"] or r["updated_at"])
            backoff = min(policy.backoff_seconds * 2 ** row["check_count"], policy.backoff_cap_seconds)
            row.update(
                check_count=row["check_count"] + 1,
                lease_until=now + timedelta(seconds=policy.lease_seconds),
                next_check_at=now + timedelta(seconds=backoff),
            )
            return {**self._view(row), "check_count": row["check_count"], "trace_parent": row["trace_parent"]}

    async def release_claim(self, claimed):
        async with self.db.operation("attempt.release_claim"), self.lock:
            row = self.rows.get((claimed["customer_id"], claimed["attempt_ref"]))
            if row and row["check_count"] == claimed["check_count"]:
                row["lease_until"] = None

    async def count_stuck(self, after_checks):
        async with self.db.operation("attempt.count_stuck"):
            counts = {}
            for row in self.rows.values():
                if row["state"] not in TERMINAL_STATES and row["check_count"] >= after_checks:
                    counts[row["state"]] = counts.get(row["state"], 0) + 1
            return counts

    async def list_for_customer(self, customer_id, *, limit=LIST_DEFAULT):
        check_customer(customer_id)
        async with self.db.operation("attempt.list"):
            rows = [
                row for row in self.rows.values() if row["customer_id"] == customer_id and row["order_id"]
            ]
            rows.sort(key=lambda row: (row["created_at"], row["attempt_ref"]), reverse=True)
            return [self._view(row) for row in rows[:limit]]

    async def get(self, customer_id, attempt_ref):
        check_key(customer_id, attempt_ref)
        async with self.db.operation("attempt.select"):
            row = self.rows.get((customer_id, attempt_ref))
            return self._view(row) if row else None


class MeshTransport(httpx.AsyncBaseTransport):
    """Routes requests to in-process apps; can inject transport faults and records requests."""

    def __init__(self, apps):
        self.apps, self.faults, self.requests = apps, [], []

    def fail(self, method, host, path_prefix, *, after_delivery=False, times=1):
        """Raise a read timeout for matching requests, before delivery or after the app handled it."""
        self.faults.append(
            {"method": method, "host": host, "prefix": path_prefix, "after": after_delivery, "times": times}
        )

    def _fault(self, request):
        for fault in self.faults:
            matches = (
                request.method == fault["method"]
                and request.url.host == fault["host"]
                and request.url.path.startswith(fault["prefix"])
            )
            if fault["times"] and matches:
                fault["times"] -= 1
                return fault

    async def handle_async_request(self, request):
        self.requests.append((request.method, request.url.host, request.url.path, request.content))
        fault = self._fault(request)
        if fault and not fault["after"]:
            raise httpx.ReadTimeout("injected", request=request)
        response = await httpx.ASGITransport(app=self.apps[request.url.host]).handle_async_request(request)
        if fault:
            await response.aread()
            raise httpx.ReadTimeout("injected after delivery", request=request)
        return response


@pytest.fixture
async def mesh():
    async with build_mesh() as built:
        yield built


@asynccontextmanager
async def build_mesh(
    orders_factory=None, attempts_factory=MemoryAttempts, saga_resume_after=0, *, keep=None, **overrides
):
    """In-process Gateway/Orders/Inventory/Payments.

    ``keep`` (a dict) carries the in-memory inventory and payments over to the next mesh built
    with the same dict, so an Orders restart can be simulated. ``overrides`` are Settings fields.
    """
    settings = Settings(
        otlp_endpoint="",
        baseline_decline_rate=0,
        orders_url="http://orders",
        inventory_url="http://inventory",
        payments_url="http://payments",
        **overrides,
    )
    exporters, readers, apps = {}, {}, {}
    logs = io.StringIO()

    def telemetry(name):
        exporters[name], readers[name] = InMemorySpanExporter(), InMemoryMetricReader()
        tel = Telemetry(name, settings, span_exporter=exporters[name], metric_reader=readers[name])
        tel.logger.handlers[0].setStream(logs)
        return tel

    def factory(kind):
        def make(runtime, tel):
            db = MemoryDatabase(runtime, tel)
            if keep is None:
                return db, kind(db)
            repository = keep.setdefault(kind.__name__, kind(db))
            repository.db = db
            return db, repository

        return make

    transport = MeshTransport(apps)
    for key, maker, kind in [
        ("inventory", inventory_app, MemoryInventory),
        ("payments", payments_app, MemoryPayments),
    ]:
        apps[key] = maker(settings, telemetry=telemetry(key), repository_factory=factory(kind))
    tel = telemetry("orders")
    apps["orders"] = orders_app(
        settings,
        telemetry=tel,
        repository_factory=orders_factory or factory(MemoryOrders),
        attempts_factory=attempts_factory,
        saga_resume_after=saga_resume_after,
        client=DependencyClient(tel, settings, httpx.AsyncClient(transport=transport)),
    )
    tel = telemetry("gateway")
    apps["gateway"] = gateway_app(
        settings,
        telemetry=tel,
        client=DependencyClient(tel, settings, httpx.AsyncClient(transport=transport)),
    )
    async with AsyncExitStack() as stack:
        for app in apps.values():
            await stack.enter_async_context(app.router.lifespan_context(app))
        client = await stack.enter_async_context(
            httpx.AsyncClient(transport=transport, base_url="http://gateway")
        )
        yield {
            "apps": apps,
            "client": client,
            "exporters": exporters,
            "readers": readers,
            "transport": transport,
            "logs": logs,
        }
