"""Legacy single-item contract stays intact until traffic and fault scenarios migrate (P1-B/P2)."""

import json


async def test_legacy_single_item_order_contract_is_unchanged(mesh):
    schema = (await mesh["client"].get("/openapi.json")).json()
    body = schema["paths"]["/api/orders"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    order_input = schema["components"]["schemas"][body["$ref"].split("/")[-1]]
    assert set(order_input["properties"]) == {"product_id", "quantity", "customer_id"}
    assert set(order_input["required"]) == {"product_id", "quantity", "customer_id"}
    assert "/api/cart/quote" in schema["paths"]
    legacy = {"product_id": "sku-001", "quantity": 2, "customer_id": "legacy-client"}
    response = await mesh["client"].post("/api/orders", json=legacy)
    assert response.status_code == 201
    assert response.json()["status"] == "completed"


async def test_quote_telemetry_uses_bounded_attributes(mesh):
    await mesh["client"].post("/api/cart/quote", json={"items": [{"product_id": "sku-002", "quantity": 3}]})
    logs = [json.loads(line) for line in mesh["logs"].getvalue().splitlines()]
    assert any(
        log["event"] == "request_completed" and log.get("http.route") == "/api/cart/quote" for log in logs
    )
    for reader in mesh["readers"].values():
        for resource in reader.get_metrics_data().resource_metrics:
            for scope in resource.scope_metrics:
                for metric in scope.metrics:
                    for point in metric.data.data_points:
                        assert "sku-002" not in str(point.attributes)
