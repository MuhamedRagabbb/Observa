import asyncio
from typing import Annotated
from uuid import uuid4

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from opentelemetry.trace import Status, StatusCode

from services.orders.saga import CheckoutSaga
from services.shared.attempts import AttemptStore
from services.shared.checkout import TERMINAL_STATES, CheckoutOrder, CheckoutResult, checkout_result
from services.shared.client import DependencyClient
from services.shared.config import Settings
from services.shared.database import Database
from services.shared.errors import AppError
from services.shared.models import OrderInput, OrderResult
from services.shared.repositories import OrderRepository
from services.shared.web import make_app

FAILURE_HTTP = {
    "payment_declined": (402, "payment_declined"),
    "payment_failed": (503, "payment_unavailable"),
    "not_placed": (409, "insufficient_stock"),
    "abandoned": (409, "attempt_abandoned"),
}


def respond(view):
    """Mirror the outcome in the HTTP status so checkout failures stay visible in telemetry."""
    result = checkout_result(view)
    if view["state"] == "completed":
        return JSONResponse(result.model_dump(), status_code=201)
    if view["state"] not in TERMINAL_STATES:
        return JSONResponse(result.model_dump(), status_code=202)
    if view["state"] == "failed":
        status = 500 if view["error_code"] == "order_persistence_error" else 503
        code = view["error_code"] or "checkout_failed"
    else:
        status, code = FAILURE_HTTP[view["state"]]
        code = view["error_code"] if view["state"] == "not_placed" and view["error_code"] else code
    raise AppError(status, code, "Checkout was not completed", result.model_dump())


def create_app(
    settings=None,
    *,
    repository_factory=None,
    telemetry=None,
    client=None,
    attempts_factory=AttemptStore,
    saga_resume_after=None,
):
    settings = settings or Settings()
    factory = repository_factory or (
        lambda runtime, tel: (db := Database(settings, tel, runtime), OrderRepository(db))
    )
    app = make_app("orders-service", settings, repository_factory=factory, telemetry=telemetry)
    tel, runtime = app.state.telemetry, app.state.runtime
    app.state.client = client or DependencyClient(tel, settings)

    def saga(request):
        # Built on first use: the database pool only exists once the lifespan has started.
        state = request.app.state
        if getattr(state, "saga", None) is None:
            state.attempts = attempts_factory(state.db)
            state.saga = CheckoutSaga(
                state.attempts, state.client, settings, tel, runtime, resume_after=saga_resume_after
            )
        return state.saga

    @app.post("/checkouts", response_model=CheckoutResult, status_code=201)
    async def place_checkout(
        data: CheckoutOrder, request: Request, idempotency_key: Annotated[str, Header(max_length=32)]
    ):
        return respond(await saga(request).place(idempotency_key, data))

    @app.get("/checkouts/{attempt_ref}", response_model=CheckoutResult)
    async def get_checkout(attempt_ref: str, customer_id: str, request: Request):
        saga(request)
        view = await request.app.state.attempts.get(customer_id, attempt_ref)
        if view is None:
            # Not found is not proof that nothing was placed; resolve explicitly to fence it.
            raise AppError(404, "attempt_not_found", "No checkout recorded for this reference yet")
        return checkout_result(view)

    @app.post("/checkouts/{attempt_ref}/resolve", response_model=CheckoutResult)
    async def resolve_checkout(attempt_ref: str, customer_id: str, request: Request):
        return respond(await saga(request).resolve(customer_id, attempt_ref))

    @app.get("/orders/{order_id}", response_model=OrderResult)
    async def get_order(order_id: str, request: Request):
        return await request.app.state.repository.get(order_id)

    @app.post("/orders", response_model=OrderResult, status_code=201)
    async def create_order(data: OrderInput, request: Request):
        order_id, reserved, paid, created = f"order-{uuid4()}", False, False, False
        payment = None
        repository, dependency = request.app.state.repository, request.app.state.client
        tel.count("orders.created")
        with tel.tracer.start_as_current_span(
            "order.workflow", attributes={"workflow.stage": "create", **runtime.attributes()}
        ):
            try:
                await repository.create(order_id, data)
                created = True
                tel.log("INFO", "order_created", "Order created", order_id=order_id)
                # Known reservation ID allows compensation even when the reserve response is lost.
                reserved = True
                await dependency.request(
                    "inventory-service",
                    settings.inventory_url,
                    "POST",
                    "/inventory/reservations",
                    body={"order_id": order_id, "product_id": data.product_id, "quantity": data.quantity},
                    retry=True,
                )
                products = await dependency.request(
                    "inventory-service", settings.inventory_url, "GET", "/inventory/products", retry=True
                )
                product = next(p for p in products if p["product_id"] == data.product_id)
                with tel.tracer.start_as_current_span(
                    "order.persistence", attributes={"workflow.stage": "persist", **runtime.attributes()}
                ) as span:
                    policy, released = runtime.settings, runtime.released
                    if policy and policy.release_version == "v2-buggy":
                        try:
                            await asyncio.wait_for(released.wait(), policy.persistence_delay_seconds)
                        except TimeoutError:
                            if data.quantity % 2 == 0:
                                span.record_exception(RuntimeError("OrderPersistenceCompatibilityError"))
                                span.set_status(
                                    Status(StatusCode.ERROR, "OrderPersistenceCompatibilityError")
                                )
                                tel.log(
                                    "ERROR",
                                    "order_persistence_failed",
                                    "Order persistence compatibility check failed",
                                    order_id=order_id,
                                    **{
                                        "error.type": "OrderPersistenceCompatibilityError",
                                        **runtime.attributes(),
                                    },
                                )
                                raise AppError(
                                    500,
                                    "order_persistence_error",
                                    "Order persistence compatibility check failed",
                                ) from None
                    await repository.update(order_id, "inventory_reserved", reservation_id=order_id)
                payment = await dependency.request(
                    "payments-service",
                    settings.payments_url,
                    "POST",
                    "/payments/charge",
                    body={
                        "order_id": order_id,
                        "idempotency_key": order_id,
                        "customer_id": data.customer_id,
                        "amount_cents": product["price_cents"] * data.quantity,
                    },
                    retry=True,
                )
                paid = True
                await repository.update(
                    order_id, "completed", reservation_id=order_id, payment_id=payment["payment_id"]
                )
                tel.count("orders.completed")
                tel.log("INFO", "order_completed", "Order completed", order_id=order_id)
                return await repository.get(order_id)
            except (Exception, asyncio.CancelledError) as exc:
                if reserved and not paid:
                    try:
                        await asyncio.shield(
                            dependency.request(
                                "inventory-service",
                                settings.inventory_url,
                                "POST",
                                f"/inventory/reservations/{order_id}/release",
                                template="/inventory/reservations/{reservation_id}/release",
                                retry=True,
                            )
                        )
                    except (AppError, asyncio.CancelledError):
                        tel.log(
                            "ERROR",
                            "inventory_release_failed",
                            "Reservation cleanup requires attention",
                            order_id=order_id,
                        )
                code = (
                    exc.code
                    if isinstance(exc, AppError)
                    else "request_cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "internal_error"
                )
                try:
                    if created:
                        await repository.update(
                            order_id,
                            "reconciliation_required" if paid else "failed",
                            payment_id=payment["payment_id"] if payment else None,
                            error_code=code,
                        )
                except AppError:
                    tel.log(
                        "ERROR",
                        "order_state_write_failed",
                        "Order state requires reconciliation",
                        order_id=order_id,
                    )
                tel.count("orders.failed")
                tel.log(
                    "ERROR",
                    "order_failed",
                    "Order workflow failed",
                    order_id=order_id,
                    **{"error.type": code},
                )
                if paid:
                    tel.log(
                        "ERROR",
                        "order_reconciliation_required",
                        "Payment committed before order persistence failed",
                        order_id=order_id,
                    )
                if not isinstance(exc, (AppError, asyncio.CancelledError)):
                    raise AppError(500, "internal_error", "Unexpected order workflow failure") from None
                raise

    return app
