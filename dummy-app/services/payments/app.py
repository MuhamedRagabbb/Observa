from fastapi import Request

from services.shared.config import Settings
from services.shared.database import Database
from services.shared.errors import AppError
from services.shared.locking import KeyLocks
from services.shared.models import PaymentInput, PaymentResult
from services.shared.repositories import PaymentRepository, stable_fraction
from services.shared.web import make_app


def create_app(settings=None, *, repository_factory=None, telemetry=None):
    settings = settings or Settings()
    factory = repository_factory or (
        lambda runtime, tel: (db := Database(settings, tel, runtime), PaymentRepository(db))
    )
    app = make_app("payments-service", settings, repository_factory=factory, telemetry=telemetry)
    tel, runtime = app.state.telemetry, app.state.runtime
    charge_locks = KeyLocks()

    @app.post("/payments/charge", response_model=PaymentResult)
    async def charge(data: PaymentInput, request: Request):
        async with charge_locks.get(data.idempotency_key):
            return await charge_once(data, request)

    async def charge_once(data: PaymentInput, request: Request):
        tel.log("INFO", "payment_attempted", "Processing simulated payment", order_id=data.order_id)
        existing = await request.app.state.repository.existing(data)
        if existing:
            result = existing
        else:
            policy = runtime.settings
            if policy and stable_fraction(data.idempotency_key + "-policy") < policy.payment_failure_rate:
                tel.count(
                    "payments.failed",
                    {"outcome": "unavailable" if policy.payment_failure_status == 503 else "declined"},
                )
                tel.log(
                    "ERROR",
                    "payment_failed",
                    "Payment processing unavailable",
                    order_id=data.order_id,
                    **{
                        "error.type": "PaymentServiceUnavailable"
                        if policy.payment_failure_status == 503
                        else "PaymentDeclined"
                    },
                )
                raise AppError(
                    policy.payment_failure_status,
                    "payment_unavailable" if policy.payment_failure_status == 503 else "payment_declined",
                    "Payment could not be processed",
                )
            decline = (
                data.customer_id.startswith("decline-")
                or stable_fraction(data.idempotency_key) < settings.baseline_decline_rate
            )
            result = await request.app.state.repository.charge(data, decline)
        if result["status"] == "declined":
            tel.count("payments.failed", {"outcome": "declined"})
            tel.log("WARN", "payment_declined", "Synthetic payment declined", order_id=data.order_id)
            raise AppError(402, "payment_declined", "Synthetic payment declined")
        tel.count("payments.succeeded", {"outcome": "success"})
        tel.log("INFO", "payment_succeeded", "Simulated payment succeeded", order_id=data.order_id)
        return result

    return app
