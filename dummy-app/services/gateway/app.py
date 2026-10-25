from fastapi import Request

from services.shared.checkout import CartLines, CartQuote, build_quote
from services.shared.client import DependencyClient
from services.shared.config import Settings
from services.shared.models import OrderInput, OrderResult, Product
from services.shared.ui import register_ui
from services.shared.web import make_app


def create_app(settings=None, *, telemetry=None, client=None):
    settings = settings or Settings()
    app = make_app("api-gateway", settings, telemetry=telemetry)
    register_ui(app, "store")
    app.state.client = client or DependencyClient(app.state.telemetry, settings)

    @app.post("/api/orders", response_model=OrderResult, status_code=201)
    async def create_order(data: OrderInput, request: Request):
        return await request.app.state.client.request(
            "orders-service", settings.orders_url, "POST", "/orders", body=data.model_dump()
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
