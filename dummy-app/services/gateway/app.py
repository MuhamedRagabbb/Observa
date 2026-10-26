from typing import Annotated

from fastapi import Header, Query, Request
from fastapi.responses import JSONResponse

from services.shared.attempts import LIST_DEFAULT, LIST_MAX, check_customer, check_key
from services.shared.checkout import CartLines, CartQuote, CheckoutInput, CheckoutResult, build_quote
from services.shared.client import DependencyClient
from services.shared.config import Settings
from services.shared.models import OrderInput, OrderResult, Product
from services.shared.ui import register_bundle, register_ui
from services.shared.web import make_app


def create_app(settings=None, *, telemetry=None, client=None):
    settings = settings or Settings()
    app = make_app("api-gateway", settings, telemetry=telemetry)
    register_ui(app, "store")
    register_bundle(app, "store")
    app.state.client = client or DependencyClient(app.state.telemetry, settings)

    @app.post("/api/orders", response_model=OrderResult, status_code=201)
    async def create_order(data: OrderInput, request: Request):
        return await request.app.state.client.request(
            "orders-service", settings.orders_url, "POST", "/orders", body=data.model_dump()
        )

    @app.get("/api/orders", response_model=list[CheckoutResult])
    async def my_orders(
        customer_id: str, request: Request, limit: Annotated[int, Query(ge=1, le=LIST_MAX)] = LIST_DEFAULT
    ):
        """My Orders: the customer's checkouts, newest first (legacy single-item orders are not listed)."""
        check_customer(customer_id)
        return await request.app.state.client.request(
            "orders-service",
            settings.orders_url,
            "GET",
            f"/checkouts?customer_id={customer_id}&limit={limit}",
            template="/checkouts",
            retry=True,
        )

    @app.get("/api/orders/{order_id}", response_model=OrderResult)
    async def get_order(order_id: str, request: Request):
        return await request.app.state.client.request(
            "orders-service",
            settings.orders_url,
            "GET",
            f"/orders/{order_id}",
            template="/orders/{order_id}",
            retry=True,
        )

    @app.post("/api/checkout", response_model=CheckoutResult, status_code=201)
    async def checkout(
        data: CheckoutInput, request: Request, idempotency_key: Annotated[str, Header(max_length=32)]
    ):
        check_key(data.customer_id, idempotency_key)
        # Contact details are validated by CheckoutInput and deliberately not forwarded.
        status, result = await request.app.state.client.request(
            "orders-service",
            settings.orders_url,
            "POST",
            "/checkouts",
            body=data.order().model_dump(),
            headers={"Idempotency-Key": idempotency_key},
            with_status=True,
        )
        return JSONResponse(result, status_code=status)

    @app.get("/api/attempts/{attempt_ref}", response_model=CheckoutResult)
    async def attempt_status(attempt_ref: str, customer_id: str, request: Request):
        check_key(customer_id, attempt_ref)
        return await request.app.state.client.request(
            "orders-service",
            settings.orders_url,
            "GET",
            f"/checkouts/{attempt_ref}?customer_id={customer_id}",
            template="/checkouts/{attempt_ref}",
            retry=True,
        )

    @app.post("/api/attempts/{attempt_ref}/resolve", response_model=CheckoutResult)
    async def resolve_attempt(attempt_ref: str, customer_id: str, request: Request):
        check_key(customer_id, attempt_ref)
        status, result = await request.app.state.client.request(
            "orders-service",
            settings.orders_url,
            "POST",
            f"/checkouts/{attempt_ref}/resolve?customer_id={customer_id}",
            template="/checkouts/{attempt_ref}/resolve",
            with_status=True,
        )
        return JSONResponse(result, status_code=status)

    @app.post("/api/cart/quote", response_model=CartQuote)
    async def cart_quote(data: CartLines, request: Request):
        products = await request.app.state.client.request(
            "inventory-service", settings.inventory_url, "GET", "/inventory/products", retry=True
        )
        return build_quote(data, products)

    @app.get("/api/products", response_model=list[Product])
    async def products(request: Request):
        return await request.app.state.client.request(
            "inventory-service", settings.inventory_url, "GET", "/inventory/products", retry=True
        )

    return app
