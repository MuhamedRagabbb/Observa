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


KEY_CONFLICT = "Reservation key belongs to different input"
CART_LINES_SQL = (
    "SELECT product_id, quantity FROM cart_reservation_lines WHERE reservation_id=$1 ORDER BY product_id"
)


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
            # Legacy and cart reservations share one key namespace.
            if await self._cart_schema_present(conn) and await conn.fetchval(
                "SELECT 1 FROM cart_reservations WHERE reservation_id=$1", data.order_id
            ):
                raise AppError(409, "idempotency_conflict", KEY_CONFLICT)
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
                    raise AppError(409, "idempotency_conflict", KEY_CONFLICT)
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

    async def reserve_lines(self, data):
        """Reserve every cart line atomically or none. Requires the cart_reservations migration."""
        key = data.order_id
        async with self.key_lock(key), self.db.connection("inventory.reserve") as conn:
            if not await self._cart_schema_present(conn):
                raise AppError(
                    503, "cart_reservations_unavailable", "Cart reservations need the 001 schema migration"
                )
            # Same per-key serialization and release tombstone as single-item reservations.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", key)
            if await conn.fetchval("SELECT 1 FROM reservation_releases WHERE reservation_id=$1", key):
                raise AppError(409, "reservation_released", "Reservation key was already released")
            if await conn.fetchval("SELECT 1 FROM reservations WHERE reservation_id=$1", key):
                raise AppError(409, "idempotency_conflict", KEY_CONFLICT)
            requested = [(item.product_id, item.quantity) for item in data.items]
            existing = await conn.fetchval(
                "SELECT status FROM cart_reservations WHERE reservation_id=$1", key
            )
            if existing:
                lines = await conn.fetch(CART_LINES_SQL, key)
                if [tuple(line) for line in lines] != requested:
                    raise AppError(409, "idempotency_conflict", KEY_CONFLICT)
                return {"reservation_id": key, "status": existing}
            # Lock product rows in product_id order so overlapping carts cannot deadlock.
            rows = await conn.fetch(
                "SELECT product_id, stock FROM products WHERE product_id = ANY($1::text[]) "
                "ORDER BY product_id FOR UPDATE",
                [product for product, _ in requested],
            )
            stock = {row["product_id"]: row["stock"] for row in rows}
            unknown = [product for product, _ in requested if product not in stock]
            if unknown:
                raise AppError(
                    404, "product_not_found", "Product was not found", {"unknown_products": unknown}
                )
            unavailable = [
                {"product_id": product, "requested": quantity, "available": stock[product]}
                for product, quantity in requested
                if stock[product] < quantity
            ]
            if unavailable:
                raise AppError(
                    409, "insufficient_stock", "Insufficient stock", {"unavailable_items": unavailable}
                )
            await conn.executemany("UPDATE products SET stock=stock-$2 WHERE product_id=$1", requested)
            await conn.execute(
                "INSERT INTO cart_reservations (reservation_id, status) VALUES ($1,'reserved')", key
            )
            await conn.executemany(
                "INSERT INTO cart_reservation_lines VALUES ($1,$2,$3)",
                [(key, product, quantity) for product, quantity in requested],
            )
            return {"reservation_id": key, "status": "reserved"}

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
            elif not row and await self._cart_schema_present(conn):
                status = await conn.fetchval(
                    "SELECT status FROM cart_reservations WHERE reservation_id=$1 FOR UPDATE", reservation_id
                )
                if status == "reserved":
                    lines = await conn.fetch(CART_LINES_SQL, reservation_id)
                    await conn.execute(
                        "UPDATE cart_reservations SET status='released' WHERE reservation_id=$1",
                        reservation_id,
                    )
                    await conn.executemany(
                        "UPDATE products SET stock=stock+$2 WHERE product_id=$1",
                        [tuple(line) for line in lines],
                    )
            # An absent reservation is already released; safe after an ambiguous reserve timeout.
            # The response is sent only after this transaction (tombstone included) commits.
            return {"reservation_id": reservation_id, "status": "released"}

    @staticmethod
    async def _cart_schema_present(conn):
        # Checked per call (not cached) so behaviour stays correct before, after and across a rollback.
        return await conn.fetchval("SELECT to_regclass('cart_reservations') IS NOT NULL")


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
