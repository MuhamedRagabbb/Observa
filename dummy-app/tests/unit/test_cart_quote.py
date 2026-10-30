import pytest


async def quote(mesh, items):
    return await mesh["client"].post("/api/cart/quote", json={"items": items})


async def test_quote_prices_available_lines_and_blocks_checkout_on_unavailable(mesh):
    response = await quote(
        mesh,
        [
            {"product_id": "sku-002", "quantity": 2},
            {"product_id": "sku-004", "quantity": 1},
            {"product_id": "sku-001", "quantity": 1},
        ],
    )
    assert response.status_code == 200
    assert len(response.headers["x-trace-id"]) == 32
    body = response.json()
    assert [(line["product_id"], line["available"], line["line_total_cents"]) for line in body["lines"]] == [
        ("sku-001", True, 1200),
        ("sku-002", True, 2400),
        ("sku-004", False, 1200),
    ]
    assert body["lines"][2]["unavailable_reason"] == "out_of_stock"
    assert body["subtotal_cents"] == 3600
    assert body["unit_count"] == 3
    assert body["unavailable_count"] == 1
    assert body["can_checkout"] is False


async def test_quote_for_fully_available_cart_allows_checkout(mesh):
    body = (await quote(mesh, [{"product_id": "sku-001", "quantity": 2}])).json()
    assert body["lines"][0]["unit_price_cents"] == 1200
    assert (body["subtotal_cents"], body["unit_count"], body["can_checkout"]) == (2400, 2, True)


async def test_quote_marks_unknown_products_and_quantity_above_stock(mesh):
    mesh["apps"]["inventory"].state.repository.stock["sku-002"] = 1
    body = (
        await quote(
            mesh, [{"product_id": "sku-999", "quantity": 1}, {"product_id": "sku-002", "quantity": 2}]
        )
    ).json()
    reasons = {line["product_id"]: line["unavailable_reason"] for line in body["lines"]}
    assert reasons == {"sku-002": "insufficient_stock", "sku-999": "unknown_product"}
    unknown = next(line for line in body["lines"] if line["product_id"] == "sku-999")
    assert unknown["unit_price_cents"] is None and unknown["line_total_cents"] is None
    assert (body["subtotal_cents"], body["unit_count"], body["can_checkout"]) == (0, 0, False)


@pytest.mark.parametrize("items", [[], [{"product_id": "sku-001", "quantity": 0}]])
async def test_invalid_quote_requests_are_rejected(mesh, items):
    response = await quote(mesh, items)
    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"


async def test_quote_does_not_reserve_stock(mesh):
    await quote(mesh, [{"product_id": "sku-001", "quantity": 5}])
    assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000
