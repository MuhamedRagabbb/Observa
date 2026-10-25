import asyncio
import io
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from services.gateway.app import create_app as gateway_app
from services.inventory.app import create_app as inventory_app
from services.orders.app import create_app as orders_app
from services.payments.app import create_app as payments_app
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

    async def existing(self, data):
        old = self.rows.get(data.idempotency_key)
        if old:
            if old[0] != data:
                raise AppError(409, "idempotency_conflict", "Conflict")
            return old[1]

    async def charge(self, data, decline):
        async with self.db.operation("payment.insert"):
            result = {
                "payment_id": "payment-" + data.order_id,
                "status": "declined" if decline else "succeeded",
            }
            self.rows.setdefault(data.idempotency_key, (data, result))
            return await self.existing(data)


class MeshTransport(httpx.AsyncBaseTransport):
    def __init__(self, apps):
        self.apps = apps

    async def handle_async_request(self, request):
        return await httpx.ASGITransport(app=self.apps[request.url.host]).handle_async_request(request)


@pytest.fixture
async def mesh():
    settings = Settings(
        otlp_endpoint="",
        baseline_decline_rate=0,
        orders_url="http://orders",
        inventory_url="http://inventory",
        payments_url="http://payments",
    )
    exporters, readers, apps = {}, {}, {}
    logs = io.StringIO()

    def telemetry(name):
        exporters[name], readers[name] = InMemorySpanExporter(), InMemoryMetricReader()
        tel = Telemetry(name, settings, span_exporter=exporters[name], metric_reader=readers[name])
        tel.logger.handlers[0].setStream(logs)
        return tel

    def factory(kind):
        return lambda runtime, tel: (db := MemoryDatabase(runtime, tel), kind(db))

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
        repository_factory=factory(MemoryOrders),
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
