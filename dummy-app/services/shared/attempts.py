"""Persistent checkout attempts (orders_db): the durable record of each multi-item checkout.

An attempt is written before any inventory or payment side effect, and every later step is a
compare-and-set on its version through the state contract in ``checkout.advance``. Contact
details are never stored. Requires orders migrations 001-003.
"""

import json
import re
from dataclasses import dataclass
from uuid import uuid4

from services.shared.checkout import TERMINAL_STATES, InvalidTransition, is_attempt_ref
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
    "total_cents",
    "failure_details",
    "created_at",
    "updated_at",
)
COLUMNS = ", ".join(FIELDS)
CUSTOMER_ID = re.compile(r"[a-zA-Z0-9_-]{1,64}")
# My Orders page size: newest first, no older pages (enough for the demo storefront).
LIST_DEFAULT, LIST_MAX = 20, 50
# Same predicate as the partial index in orders migration 003, so the planner can use it.
OPEN_STATES = "state NOT IN (" + ", ".join(f"'{state}'" for state in sorted(TERMINAL_STATES)) + ")"


@dataclass(frozen=True)
class ClaimPolicy:
    """When the reconciler may take an attempt, and how long it keeps it."""

    stall_seconds: float  # no progress for this long: no request is still driving it
    lease_seconds: float  # longer than one drive; another reconciler waits this long
    backoff_seconds: float = 5
    backoff_cap_seconds: float = 60


def check_customer(customer_id):
    if not CUSTOMER_ID.fullmatch(customer_id):
        raise AppError(422, "invalid_customer_id", "Customer ID is malformed")


def check_key(customer_id, attempt_ref):
    if not (CUSTOMER_ID.fullmatch(customer_id) and is_attempt_ref(attempt_ref)):
        raise AppError(422, "invalid_attempt_key", "Customer or attempt reference is malformed")


class AttemptStore:
    def __init__(self, db):
        self.db = db

    async def begin(self, attempt_ref, data, *, trace_parent=None):
        """Create the attempt, or return it unchanged when the same request is replayed.

        ``trace_parent`` (W3C) lets later reconciliation link back to the originating trace.
        """
        check_key(data.customer_id, attempt_ref)
        order_id, request_hash = f"order-{uuid4()}", data.request_hash()
        order_suffix = order_id[6:10].upper()  # first 4 hex digits of the UUID, e.g. OBS-1042-7F3A
        async with self.db.connection("attempt.begin", hold=True) as conn:
            row = await conn.fetchrow(
                f"INSERT INTO checkout_attempts "
                f"(customer_id, attempt_ref, request_hash, order_id, order_number, state, trace_parent) "
                f"VALUES ($1, $2, $3, $4, 'OBS-' || nextval('order_number_seq') || '-' || $5, 'received', $6) "
                f"ON CONFLICT (customer_id, attempt_ref) DO NOTHING RETURNING {COLUMNS}",
                data.customer_id,
                attempt_ref,
                request_hash,
                order_id,
                order_suffix,
                trace_parent,
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

    async def advance(
        self, customer_id, attempt_ref, event, *, expected_version, error_code=None, prices=None, details=None
    ):
        """Apply one saga event if the caller saw the latest version; committed before returning.

        ``prices`` (product_id -> server unit price) stores line prices and the total in the same
        transaction as the step, so a recovered attempt always charges the persisted amount.
        ``details`` keeps structured failure data (e.g. unavailable items) for idempotent replays.
        """
        check_key(customer_id, attempt_ref)
        async with self.db.connection("attempt.advance") as conn:
            row = await conn.fetchrow(
                "SELECT state, version, order_id FROM checkout_attempts "
                "WHERE customer_id=$1 AND attempt_ref=$2 FOR UPDATE",
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
            total = await self._price(conn, row["order_id"], prices) if prices is not None else None
            updated = await conn.fetchrow(
                f"UPDATE checkout_attempts SET state=$3, version=version+1, "
                f"error_code=COALESCE($4, error_code), total_cents=COALESCE($5, total_cents), "
                f"failure_details=COALESCE($6::jsonb, failure_details), updated_at=now() "
                f"WHERE customer_id=$1 AND attempt_ref=$2 RETURNING {COLUMNS}",
                customer_id,
                attempt_ref,
                state,
                error_code,
                total,
                json.dumps(details) if details is not None else None,
            )
            return await self._view(conn, updated, replayed=False)

    @staticmethod
    async def _price(conn, order_id, prices):
        lines = await conn.fetch("SELECT product_id, quantity FROM order_items WHERE order_id=$1", order_id)
        if any(line["product_id"] not in prices for line in lines):
            raise AppError(502, "price_unavailable", "A line price was not available")
        await conn.executemany(
            "UPDATE order_items SET unit_price_cents=$3 WHERE order_id=$1 AND product_id=$2",
            [(order_id, line["product_id"], prices[line["product_id"]]) for line in lines],
        )
        return sum(prices[line["product_id"]] * line["quantity"] for line in lines)

    async def claims_supported(self):
        """Whether orders migration 003 (reconciler scheduling columns) has been applied."""
        async with self.db.connection("attempt.schema_check") as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema=current_schema() AND table_name='checkout_attempts' "
                    "AND column_name='lease_until'"
                )
            )

    async def claim_stalled(self, policy, *, now=None):
        """Lease one attempt that is open, stalled and due for a check; None when there is none.

        Each claim counts a check and schedules the next one with exponential backoff. The
        attempt's state and version are untouched, so a request that is still running keeps
        winning its compare-and-set steps. Locked rows are skipped, so concurrent reconcilers
        never claim the same attempt. ``now`` defaults to the database clock (tests pass one).
        """
        async with self.db.connection("attempt.claim") as conn:
            row = await conn.fetchrow(
                f"UPDATE checkout_attempts a SET check_count = a.check_count + 1, "
                f"lease_until = COALESCE($1::timestamptz, now()) + make_interval(secs => $3::float8), "
                f"next_check_at = COALESCE($1::timestamptz, now()) + make_interval(secs => LEAST($4::float8 * power(2, a.check_count), $5::float8)) "
                f"FROM (SELECT customer_id, attempt_ref FROM checkout_attempts "
                f"WHERE {OPEN_STATES} AND updated_at <= COALESCE($1::timestamptz, now()) - make_interval(secs => $2::float8) "
                f"AND (next_check_at IS NULL OR next_check_at <= $1) "
                f"AND (lease_until IS NULL OR lease_until <= $1) "
                f"ORDER BY COALESCE(next_check_at, updated_at) LIMIT 1 FOR UPDATE SKIP LOCKED) due "
                f"WHERE a.customer_id = due.customer_id AND a.attempt_ref = due.attempt_ref "
                f"RETURNING {', '.join('a.' + field for field in FIELDS)}, a.check_count, a.trace_parent",
                now,
                float(policy.stall_seconds),
                float(policy.lease_seconds),
                float(policy.backoff_seconds),
                float(policy.backoff_cap_seconds),
            )
            if row is None:
                return None
            view = await self._view(conn, row, replayed=False)
            return {**view, "check_count": row["check_count"], "trace_parent": row["trace_parent"]}

    async def release_claim(self, claimed):
        """End a lease early; a no-op once a newer claim has taken the attempt over."""
        async with self.db.connection("attempt.release_claim") as conn:
            await conn.execute(
                "UPDATE checkout_attempts SET lease_until = NULL "
                "WHERE customer_id=$1 AND attempt_ref=$2 AND check_count=$3",
                claimed["customer_id"],
                claimed["attempt_ref"],
                claimed["check_count"],
            )

    async def count_stuck(self, after_checks):
        """Open attempts checked at least ``after_checks`` times, by state (at most nine states)."""
        async with self.db.connection("attempt.count_stuck") as conn:
            rows = await conn.fetch(
                f"SELECT state, count(*) AS n FROM checkout_attempts "
                f"WHERE {OPEN_STATES} AND check_count >= $1 GROUP BY state",
                after_checks,
            )
            return {row["state"]: row["n"] for row in rows}

    async def list_for_customer(self, customer_id, *, limit=LIST_DEFAULT):
        """My Orders: the customer's recorded checkouts, newest first.

        A resolve fence for a checkout that never arrived has no order, so it is not listed.
        """
        check_customer(customer_id)
        async with self.db.connection("attempt.list") as conn:
            rows = await conn.fetch(
                f"SELECT {COLUMNS} FROM checkout_attempts WHERE customer_id=$1 AND order_id IS NOT NULL "
                f"ORDER BY created_at DESC, attempt_ref DESC LIMIT $2",
                customer_id,
                limit,
            )
            return [await self._view(conn, row, replayed=False) for row in rows]

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
            "SELECT product_id, quantity, unit_price_cents FROM order_items WHERE order_id=$1 ORDER BY product_id",
            row["order_id"],
        )
        view = {key: row[key] for key in FIELDS if key not in ("request_hash", "failure_details")}
        details = json.loads(row["failure_details"]) if row["failure_details"] else None
        lines = [{key: value for key, value in dict(item).items() if value is not None} for item in items]
        return {**view, "details": details, "items": lines, "replayed": replayed}
