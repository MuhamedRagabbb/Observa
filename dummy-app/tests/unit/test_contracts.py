import asyncio
import json
import time
from uuid import uuid4

import httpx
import pytest

from services.shared.models import RuntimeUpdate

ORDER = {"product_id": "sku-001", "quantity": 2, "customer_id": "test-user"}


async def test_checkout_status_correlation_and_trace_mesh(mesh):
    response = await mesh["client"].post("/api/orders", json=ORDER, headers={"x-request-id": "req-contract"})
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "completed"
    stored = await mesh["client"].get("/api/orders/" + order["order_id"])
    assert stored.json() == order
    spans = []
    for exporter in mesh["exporters"].values():
        for app in mesh["apps"].values():
            app.state.telemetry.traces.force_flush()
        spans.extend(exporter.get_finished_spans())
    trace_id = int(response.headers["x-trace-id"], 16)
    checkout = [span for span in spans if span.context.trace_id == trace_id]
    assert {span.resource.attributes["service.name"] for span in checkout} == {
        "gateway",
        "orders",
        "inventory",
        "payments",
    }
    assert any(span.name.startswith("db.") for span in checkout)
    assert all(
        span.parent is not None for span in checkout if span.resource.attributes["service.name"] != "gateway"
    )
    logs = [json.loads(line) for line in mesh["logs"].getvalue().splitlines()]
    requests = [
        log for log in logs if log["event"] == "request_completed" and log["request_id"] == "req-contract"
    ]
    assert len(requests) == 5  # Gateway, Orders, inventory reserve/list, Payments
    for log in requests:
        assert {
            "timestamp",
            "level",
            "service.name",
            "event",
            "request_id",
            "trace_id",
            "span_id",
        } <= log.keys()
        assert log["trace_id"] == response.headers["x-trace-id"]
        assert len(log["span_id"]) == 16


@pytest.mark.parametrize(
    "override",
    [
        {"quantity": 0},
        {"quantity": 11},
        {"quantity": "2"},
        {"customer_id": "invalid\nvalue"},
        {"product_id": "bad"},
    ],
)
async def test_order_validation(mesh, override):
    response = await mesh["client"].post("/api/orders", json={**ORDER, **override})
    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"
    assert response.json()["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize(
    ("product", "status", "code"),
    [("sku-004", 409, "insufficient_stock"), ("sku-999", 404, "product_not_found")],
)
async def test_stock_rejections(mesh, product, status, code):
    response = await mesh["client"].post("/api/orders", json={**ORDER, "product_id": product})
    assert response.status_code == status
    assert response.json()["error_code"] == code
    assert next(iter(mesh["apps"]["orders"].state.repository.rows.values()))["status"] == "failed"


async def test_decline_compensates_and_persists_failure(mesh):
    response = await mesh["client"].post("/api/orders", json={**ORDER, "customer_id": "decline-test"})
    assert response.status_code == 402
    assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000
    assert (
        next(iter(mesh["apps"]["orders"].state.repository.rows.values()))["error_code"] == "payment_declined"
    )


async def test_inventory_and_payment_idempotency(mesh):
    async with httpx.AsyncClient(transport=mesh["transport"]) as client:
        order_id = "order-" + str(uuid4())
        reservation = {"order_id": order_id, "product_id": "sku-001", "quantity": 2}
        results = await asyncio.gather(
            *[client.post("http://inventory/inventory/reservations", json=reservation) for _ in range(10)]
        )
        assert all(response.status_code == 201 for response in results)
        assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 998
        assert (
            await client.post("http://inventory/inventory/reservations", json={**reservation, "quantity": 3})
        ).status_code == 409
        releases = await asyncio.gather(
            *[client.post(f"http://inventory/inventory/reservations/{order_id}/release") for _ in range(10)]
        )
        assert all(response.status_code == 200 for response in releases)
        assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000
        payment = {
            "order_id": order_id,
            "idempotency_key": order_id,
            "customer_id": "test",
            "amount_cents": 2400,
        }
        first = await client.post("http://payments/payments/charge", json=payment)
        second = await client.post("http://payments/payments/charge", json=payment)
        assert first.json() == second.json()
        assert (
            await client.post("http://payments/payments/charge", json={**payment, "amount_cents": 100})
        ).status_code == 409


async def test_bounded_metric_attributes(mesh):
    response = await mesh["client"].post("/api/orders", json=ORDER)
    for _ in range(4):
        await mesh["client"].get("/api/orders/" + response.json()["order_id"])
    names = set()
    for reader in mesh["readers"].values():
        for resource in reader.get_metrics_data().resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    names.add(metric.name)
                    for point in metric.data.data_points:
                        assert (
                            not {"request_id", "order_id", "trace_id", "customer_id", "url", "scenario"}
                            & point.attributes.keys()
                        )
                        assert response.json()["order_id"] not in str(point.attributes)
    assert {
        "http.server.requests",
        "http.server.duration",
        "dependency.calls",
        "dependency.duration",
        "orders.completed",
        "payments.succeeded",
    } <= names


async def test_payments_policy_no_delay_and_recovery(mesh):
    runtime = mesh["apps"]["payments"].state.runtime
    await runtime.update(RuntimeUpdate(expires_at=time.time() + 30, payment_failure_rate=1))
    start = time.perf_counter()
    response = await mesh["client"].post("/api/orders", json=ORDER)
    assert response.status_code == 503
    assert time.perf_counter() - start < 0.5
    assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000
    await runtime.update(None)
    assert (await mesh["client"].post("/api/orders", json=ORDER)).status_code == 201


async def test_bad_release_signature_delay_and_rollback(mesh):
    runtime = mesh["apps"]["orders"].state.runtime
    deployment = runtime.deployment_id
    await runtime.update(
        RuntimeUpdate(expires_at=time.time() + 30, release_version="v2-buggy", persistence_delay_seconds=1)
    )
    assert runtime.deployment_id != deployment
    start = time.perf_counter()
    response = await mesh["client"].post("/api/orders", json=ORDER)
    assert time.perf_counter() - start >= 1
    assert response.status_code == 500
    assert response.json()["error_code"] == "order_persistence_error"
    assert "OrderPersistenceCompatibilityError" in mesh["logs"].getvalue()
    assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000
    await runtime.update(None)
    assert runtime.release_version == "v1"
    assert (await mesh["client"].post("/api/orders", json=ORDER)).status_code == 201


async def test_unknown_routes_and_openapi(mesh):
    assert (await mesh["client"].get("/missing/unique-value")).json()["error_code"] == "http_error"
    schema = (await mesh["client"].get("/openapi.json")).json()
    assert "201" in schema["paths"]["/api/orders"]["post"]["responses"]
    assert "422" in schema["paths"]["/api/orders"]["post"]["responses"]
    assert "X-Trace-ID" in schema["paths"]["/api/orders"]["post"]["responses"]["201"]["headers"]
    assert not any(path.startswith("/_control") for path in schema["paths"])


async def test_internal_control_auth_and_telemetry_isolation(mesh):
    async with httpx.AsyncClient(transport=mesh["transport"], base_url="http://orders") as client:
        response = await client.put(
            "/_control/runtime", json={"expires_at": time.time() + 30, "db_hold_seconds": 1}
        )
        assert response.status_code == 401
        assert mesh["apps"]["orders"].state.runtime.settings is None
        assert (
            await client.put(
                "/_control/runtime",
                json={"expires_at": time.time() + 30, "db_hold_seconds": 1},
                headers={"x-control-token": "local-control-only"},
            )
        ).status_code == 200
        assert (
            await client.delete("/_control/runtime", headers={"x-control-token": "local-control-only"})
        ).status_code == 200
    logs = mesh["logs"].getvalue()
    assert "/_control" not in logs
    assert "local-control-only" not in logs
