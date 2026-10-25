"""The new checkout flow keeps the existing telemetry vocabulary and bounded attributes."""

import time

from services.shared.models import RuntimeUpdate

CONTACT = {"name": "Demo name", "email": "demo@example.com"}
REF = "ATT-7F3A-0915"


async def run_checkouts(mesh):
    items = [{"product_id": "sku-001", "quantity": 1}, {"product_id": "sku-002", "quantity": 2}]
    ok = await mesh["client"].post(
        "/api/checkout",
        json={"customer_id": "web-user-1", "contact": CONTACT, "items": items},
        headers={"Idempotency-Key": REF},
    )
    await mesh["apps"]["payments"].state.runtime.update(
        RuntimeUpdate(expires_at=time.time() + 30, payment_failure_rate=1, payment_failure_status=503)
    )
    failed = await mesh["client"].post(
        "/api/checkout",
        json={"customer_id": "web-user-1", "contact": CONTACT, "items": items},
        headers={"Idempotency-Key": "ATT-0001-0002"},
    )
    return ok, failed


async def test_checkout_metrics_keep_names_and_bounded_attributes(mesh):
    ok, failed = await run_checkouts(mesh)
    assert (ok.status_code, failed.status_code) == (201, 503)
    forbidden = [REF, "ATT-0001-0002", "web-user-1", ok.json()["order_id"], "demo@example.com", "Demo name"]
    names, routes = set(), set()
    for reader in mesh["readers"].values():
        for resource in reader.get_metrics_data().resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    names.add(metric.name)
                    for point in metric.data.data_points:
                        text = str(dict(point.attributes))
                        assert not any(value in text for value in forbidden), (metric.name, text)
                        if "http.route" in point.attributes:
                            routes.add(point.attributes["http.route"])
    assert {
        "orders.created",
        "orders.completed",
        "orders.failed",
        "payments.succeeded",
        "payments.failed",
    } <= names
    assert {"/api/checkout", "/checkouts", "/payments/{idempotency_key}/void"} <= routes
    assert not any("order-" in route or "ATT-" in route for route in routes)


async def test_checkout_spans_carry_no_identifiers_or_contact(mesh):
    ok, _ = await run_checkouts(mesh)
    for app in mesh["apps"].values():
        app.state.telemetry.traces.force_flush()
    spans = [span for exporter in mesh["exporters"].values() for span in exporter.get_finished_spans()]
    assert any(span.name == "order.workflow" for span in spans)
    forbidden = [REF, "demo@example.com", "Demo name", ok.json()["order_id"]]
    for span in spans:
        text = str(dict(span.attributes)) + span.name
        assert not any(value in text for value in forbidden), text
