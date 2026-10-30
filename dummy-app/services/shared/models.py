from typing import Literal

from pydantic import BaseModel, Field


class ErrorBody(BaseModel):
    error_code: str
    message: str
    request_id: str
    trace_id: str | None = None
    details: dict | None = None


ERRORS = {code: {"model": ErrorBody} for code in (400, 401, 404, 409, 422, 500, 502, 503, 504)}


class HealthResult(BaseModel):
    status: Literal["ready", "unavailable"]
    service: str
    service_version: str = Field(alias="service.version")
    deployment_id: str = Field(alias="deployment.id")


class OrderInput(BaseModel):
    product_id: str = Field(pattern=r"^sku-[0-9]{3}$")
    quantity: int = Field(ge=1, le=10, strict=True)
    customer_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")


class OrderResult(BaseModel):
    order_id: str
    product_id: str
    quantity: int
    status: Literal["pending", "inventory_reserved", "completed", "failed", "reconciliation_required"]
    reservation_id: str | None = None
    payment_id: str | None = None
    error_code: str | None = None


class ReservationInput(BaseModel):
    order_id: str = Field(pattern=r"^order-[a-f0-9-]{36}$")
    product_id: str = Field(pattern=r"^sku-[0-9]{3}$")
    quantity: int = Field(ge=1, le=10, strict=True)


class ReservationResult(BaseModel):
    reservation_id: str
    status: Literal["reserved", "released"]


class Product(BaseModel):
    product_id: str
    name: str
    price_cents: int
    stock: int


class PaymentInput(BaseModel):
    order_id: str = Field(pattern=r"^order-[a-f0-9-]{36}$")
    idempotency_key: str = Field(min_length=1, max_length=100)
    customer_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    amount_cents: int = Field(gt=0, le=10000000, strict=True)


class PaymentResult(BaseModel):
    payment_id: str
    status: Literal["succeeded", "declined"]


class VoidInput(BaseModel):
    order_id: str = Field(pattern=r"^order-[a-f0-9-]{36}$")


class PaymentRecord(BaseModel):
    payment_id: str
    order_id: str
    status: Literal["succeeded", "declined", "voided"]


class VoidResult(BaseModel):
    payment_id: str
    status: Literal["succeeded", "declined", "voided"]


class RuntimeUpdate(BaseModel):
    expires_at: float = Field(gt=0)
    db_hold_seconds: float = Field(default=0, ge=0, le=15)
    payment_failure_rate: float = Field(default=0, ge=0, le=1)
    payment_failure_status: Literal[402, 503] = 503
    release_version: Literal["v1", "v2-buggy"] = "v1"
    persistence_delay_seconds: float = Field(default=1.5, ge=1, le=2)
