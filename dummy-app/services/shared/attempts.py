"""Persistent checkout attempts (orders_db): the durable record of each multi-item checkout.

An attempt is written before any inventory or payment side effect, and every later step is a
compare-and-set on its version through the state contract in ``checkout.advance``. Contact
details are never stored. Requires orders migration 001.
"""

import re
from uuid import uuid4

from services.shared.checkout import InvalidTransition, is_attempt_ref
from services.shared.checkout import advance as next_state
from services.shared.errors import AppError

FIELDS = (
    "customer_id",
    "attempt_ref",
    "request_hash",
    "order_id",
    "order_number",
    "state",
    "version",
    "error_code",
)
COLUMNS = ", ".join(FIELDS)
CUSTOMER_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")


def check_key(customer_id, attempt_ref):
    if not (CUSTOMER_ID.fullmatch(customer_id) and is_attempt_ref(attempt_ref)):
        raise AppError(422, "invalid_attempt_key", "Customer or attempt reference is malformed")


class AttemptStore:
    def __init__(self, db):
        self.db = db

    async def begin(self, attempt_ref, data):
        """Create the attempt, or return it unchanged when the same request is replayed."""
        check_key(data.customer_id, attempt_ref)
        order_id, request_hash = f"order-{uuid4()}", data.request_hash()
        order_suffix = order_id[6:10].upper()  # first 4 hex digits of the UUID, e.g. OBS-1042-7F3A
        async with self.db.connection("attempt.begin") as conn:
            row = await conn.fetchrow(
                f"INSERT INTO checkout_attempts (customer_id, attempt_ref, request_hash, order_id, order_number, state) "
                f"VALUES ($1, $2, $3, $4, 'OBS-' || nextval('order_number_seq') || '-' || $5, 'received') "
                f"ON CONFLICT (customer_id, attempt_ref) DO NOTHING RETURNING {COLUMNS}",
                data.customer_id,
                attempt_ref,
                request_hash,
                order_id,
                order_suffix,
            )
            if row:
                await conn.executemany(
                    "INSERT INTO order_items (order_id, product_id, quantity) VALUES ($1, $2, $3)",
                    [(order_id, item.product_id, item.quantity) for item in data.items],
                )
                return await self._view(conn, row, replayed=False)
            existing = await self._row(conn, data.customer_id, attempt_ref)
            if existing["state"] == "abandoned":
                raise AppError(
                    409, "attempt_abandoned", "Attempt reference was already resolved as not placed"
                )
            if existing["request_hash"] != request_hash:
                raise AppError(
                    409, "idempotency_conflict", "Attempt reference belongs to a different checkout"
                )
            return await self._view(conn, existing, replayed=True)

    async def abandon(self, customer_id, attempt_ref):
        """Resolve an unconfirmed attempt: fence it if absent, otherwise return it unchanged.

        After this returns ``abandoned``, a late begin with the same key is refused, so the
        customer can safely retry with a new attempt reference.
        """
        check_key(customer_id, attempt_ref)
        async with self.db.connection("attempt.abandon") as conn:
            row = await conn.fetchrow(
                f"INSERT INTO checkout_attempts (customer_id, attempt_ref, state) VALUES ($1, $2, $3) "
                f"ON CONFLICT (customer_id, attempt_ref) DO NOTHING RETURNING {COLUMNS}",
                customer_id,
                attempt_ref,
                next_state(None, "abandon"),
            )
            return await self._view(
                conn, row or await self._row(conn, customer_id, attempt_ref), replayed=False
            )

    async def advance(self, customer_id, attempt_ref, event, *, expected_version, error_code=None):
        """Apply one saga event if the caller saw the latest version; committed before returning."""
        check_key(customer_id, attempt_ref)
        async with self.db.connection("attempt.advance") as conn:
            row = await conn.fetchrow(
                "SELECT state, version FROM checkout_attempts WHERE customer_id=$1 AND attempt_ref=$2 FOR UPDATE",
                customer_id,
                attempt_ref,
            )
            if not row:
                raise AppError(404, "attempt_not_found", "Checkout attempt was not found")
            if row["version"] != expected_version:
                raise AppError(409, "stale_attempt", "Checkout attempt changed since it was read")
            try:
                state = next_state(row["state"], event)
            except InvalidTransition:
                raise AppError(409, "invalid_transition", "Checkout step is not allowed now") from None
            updated = await conn.fetchrow(
                f"UPDATE checkout_attempts SET state=$3, version=version+1, "
                f"error_code=COALESCE($4, error_code), updated_at=now() "
                f"WHERE customer_id=$1 AND attempt_ref=$2 RETURNING {COLUMNS}",
                customer_id,
                attempt_ref,
                state,
                error_code,
            )
            return await self._view(conn, updated, replayed=False)

    async def get(self, customer_id, attempt_ref):
        check_key(customer_id, attempt_ref)
        async with self.db.connection("attempt.select") as conn:
            row = await self._row(conn, customer_id, attempt_ref)
            return await self._view(conn, row, replayed=False) if row else None

    @staticmethod
    async def _row(conn, customer_id, attempt_ref):
        return await conn.fetchrow(
            f"SELECT {COLUMNS} FROM checkout_attempts WHERE customer_id=$1 AND attempt_ref=$2",
            customer_id,
            attempt_ref,
        )

    @staticmethod
    async def _view(conn, row, *, replayed):
        items = await conn.fetch(
            "SELECT product_id, quantity FROM order_items WHERE order_id=$1 ORDER BY product_id",
            row["order_id"],
        )
        view = {key: row[key] for key in FIELDS if key != "request_hash"}
        return {**view, "items": [dict(item) for item in items], "replayed": replayed}
