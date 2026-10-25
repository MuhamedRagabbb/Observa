import hashlib
from uuid import uuid4

from services.shared.errors import AppError
from services.shared.locking import KeyLocks


def stable_fraction(key):
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big") / 2**64


class OrderRepository:
    def __init__(self, db):
        self.db = db

    async def create(self, order_id, data):
        async with self.db.connection("order.insert", hold=True) as conn:
            await conn.execute(
                "INSERT INTO orders (order_id,product_id,quantity,customer_id,status) VALUES ($1,$2,$3,$4,'pending')",
                order_id,
                data.product_id,
                data.quantity,
                data.customer_id,
            )

    async def update(self, order_id, status, reservation_id=None, payment_id=None, error_code=None):
        async with self.db.connection("order.persist") as conn:
            await conn.execute(
                "UPDATE orders SET status=$2,reservation_id=COALESCE($3,reservation_id),"
                "payment_id=COALESCE($4,payment_id),error_code=$5,updated_at=now() WHERE order_id=$1",
                order_id,
                status,
                reservation_id,
                payment_id,
                error_code,
            )

    async def get(self, order_id):
        async with self.db.connection("order.select") as conn:
            row = await conn.fetchrow(
                "SELECT order_id,product_id,quantity,status,reservation_id,payment_id,error_code FROM orders WHERE order_id=$1",
                order_id,
            )
        if not row:
            raise AppError(404, "order_not_found", "Order was not found")
        return dict(row)


class InventoryRepository:
    def __init__(self, db):
        self.db = db
        # Bound lock memory and keep duplicate HTTP retries from occupying every DB lease.
        self.key_locks = KeyLocks()

    def key_lock(self, reservation_id):
        return self.key_locks.get(reservation_id)

    async def products(self):
        async with self.db.connection("products.select") as conn:
            return [dict(row) for row in await conn.fetch("SELECT * FROM products ORDER BY product_id")]

    async def reserve(self, data):
        async with self.key_lock(data.order_id), self.db.connection("inventory.reserve") as conn:
            # Serialize one key across different products and late timeout compensation.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", data.order_id)
            cancelled = await conn.fetchval(
                "SELECT 1 FROM reservation_releases WHERE reservation_id=$1", data.order_id
            )
            if cancelled:
                raise AppError(409, "reservation_released", "Reservation key was already released")
            product = await conn.fetchrow(
                "SELECT * FROM products WHERE product_id=$1 FOR UPDATE", data.product_id
            )
            if not product:
                raise AppError(404, "product_not_found", "Product was not found")
            existing = await conn.fetchrow(
                "SELECT * FROM reservations WHERE reservation_id=$1", data.order_id
            )
            if existing:
                if existing["product_id"] != data.product_id or existing["quantity"] != data.quantity:
                    raise AppError(409, "idempotency_conflict", "Reservation key belongs to different input")
                return {"reservation_id": data.order_id, "status": existing["status"]}
            if product["stock"] < data.quantity:
                raise AppError(409, "insufficient_stock", "Insufficient stock")
            await conn.execute(
                "UPDATE products SET stock=stock-$2 WHERE product_id=$1", data.product_id, data.quantity
            )
            await conn.execute(
                "INSERT INTO reservations VALUES ($1,$2,$3,'reserved')",
                data.order_id,
                data.product_id,
                data.quantity,
            )
            return {"reservation_id": data.order_id, "status": "reserved"}

    async def release(self, reservation_id):
        async with self.key_lock(reservation_id), self.db.connection("inventory.release") as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", reservation_id)
            await conn.execute(
                "INSERT INTO reservation_releases VALUES ($1) ON CONFLICT DO NOTHING", reservation_id
            )
            row = await conn.fetchrow(
                "SELECT * FROM reservations WHERE reservation_id=$1 FOR UPDATE", reservation_id
            )
            if row and row["status"] == "reserved":
                await conn.execute(
                    "UPDATE reservations SET status='released' WHERE reservation_id=$1", reservation_id
                )
                await conn.execute(
                    "UPDATE products SET stock=stock+$2 WHERE product_id=$1",
                    row["product_id"],
                    row["quantity"],
                )
            # An absent reservation is already released; safe after an ambiguous reserve timeout.
            return {"reservation_id": reservation_id, "status": "released"}


class PaymentRepository:
    def __init__(self, db):
        self.db = db

    async def existing(self, data):
        async with self.db.connection("payment.select") as conn:
            row = await conn.fetchrow("SELECT * FROM payments WHERE idempotency_key=$1", data.idempotency_key)
        return self.check(row, data) if row else None

    @staticmethod
    def check(row, data):
        if any(row[key] != getattr(data, key) for key in ("order_id", "customer_id", "amount_cents")):
            raise AppError(409, "idempotency_conflict", "Payment key belongs to different input")
        return {"payment_id": row["payment_id"], "status": row["status"]}

    async def charge(self, data, decline):
        async with self.db.connection("payment.insert") as conn:
            await conn.execute(
                "INSERT INTO payments (payment_id,idempotency_key,order_id,customer_id,amount_cents,status) "
                "VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT (idempotency_key) DO NOTHING",
                f"payment-{uuid4()}",
                data.idempotency_key,
                data.order_id,
                data.customer_id,
                data.amount_cents,
                "declined" if decline else "succeeded",
            )
            row = await conn.fetchrow("SELECT * FROM payments WHERE idempotency_key=$1", data.idempotency_key)
            return self.check(row, data)
