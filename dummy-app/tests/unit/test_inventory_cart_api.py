from uuid import uuid4

import httpx
import pytest


async def reserve(mesh, body):
    async with httpx.AsyncClient(transport=mesh["transport"]) as client:
        return await client.post("http://inventory/inventory/reservations", json=body)


async def release(mesh, reservation_id):
    async with httpx.AsyncClient(transport=mesh["transport"]) as client:
        return await client.post(f"http://inventory/inventory/reservations/{reservation_id}/release")


def items(*lines):
    return [{"product_id": product, "quantity": quantity} for product, quantity in lines]


async def test_cart_reservation_and_release_over_http(mesh):
    stock = mesh["apps"]["inventory"].state.repository.stock
    order_id = f"order-{uuid4()}"
    response = await reserve(mesh, {"order_id": order_id, "items": items(("sku-001", 2), ("sku-002", 3))})
    assert response.status_code == 201
    assert response.json() == {"reservation_id": order_id, "status": "reserved"}
    assert (stock["sku-001"], stock["sku-002"]) == (998, 997)
    assert (await release(mesh, order_id)).json()["status"] == "released"
    assert (stock["sku-001"], stock["sku-002"]) == (1000, 1000)


async def test_unavailable_items_are_reported_in_error_details(mesh):
    response = await reserve(
        mesh, {"order_id": f"order-{uuid4()}", "items": items(("sku-001", 1), ("sku-004", 2))}
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "insufficient_stock"
    assert body["details"] == {
        "unavailable_items": [{"product_id": "sku-004", "requested": 2, "available": 0}]
    }
    assert mesh["apps"]["inventory"].state.repository.stock["sku-001"] == 1000


async def test_legacy_error_bodies_have_no_details_field(mesh):
    response = await reserve(mesh, {"order_id": f"order-{uuid4()}", "product_id": "sku-004", "quantity": 1})
    assert response.status_code == 409
    assert set(response.json()) == {"error_code", "message", "request_id", "trace_id"}


@pytest.mark.parametrize(
    "body",
    [
        {"order_id": "order-x", "items": items(("sku-001", 1))},
        {"order_id": f"order-{uuid4()}", "items": []},
        {
            "order_id": f"order-{uuid4()}",
            "items": items(("sku-001", 1)),
            "product_id": "sku-001",
            "quantity": 1,
        },
    ],
)
async def test_invalid_cart_reservations_are_rejected(mesh, body):
    response = await reserve(mesh, body)
    assert response.status_code == 422
    assert response.json()["error_code"] == "validation_error"
