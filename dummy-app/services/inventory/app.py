from fastapi import Request

from services.shared.config import Settings
from services.shared.database import Database
from services.shared.errors import AppError
from services.shared.models import Product, ReservationInput, ReservationResult
from services.shared.repositories import InventoryRepository
from services.shared.web import make_app


def create_app(settings=None, *, repository_factory=None, telemetry=None):
    settings = settings or Settings()
    factory = repository_factory or (
        lambda runtime, tel: (db := Database(settings, tel, runtime), InventoryRepository(db))
    )
    app = make_app("inventory-service", settings, repository_factory=factory, telemetry=telemetry)
    tel = app.state.telemetry

    @app.get("/inventory/products", response_model=list[Product])
    async def products(request: Request):
        return await request.app.state.repository.products()

    @app.post("/inventory/reservations", response_model=ReservationResult, status_code=201)
    async def reserve(data: ReservationInput, request: Request):
        tel.log("INFO", "inventory_reservation_started", "Reserving inventory", order_id=data.order_id)
        try:
            result = await request.app.state.repository.reserve(data)
        except AppError as exc:
            tel.log(
                "WARN",
                "inventory_reservation_failed",
                "Inventory reservation rejected",
                order_id=data.order_id,
                **{"error.type": exc.code},
            )
            raise
        tel.log("INFO", "inventory_reservation_succeeded", "Inventory reserved", order_id=data.order_id)
        return result

    @app.post("/inventory/reservations/{reservation_id}/release", response_model=ReservationResult)
    async def release(reservation_id: str, request: Request):
        result = await request.app.state.repository.release(reservation_id)
        tel.log(
            "INFO",
            "inventory_reservation_released",
            "Inventory reservation released",
            reservation_id=reservation_id,
        )
        return result

    return app
