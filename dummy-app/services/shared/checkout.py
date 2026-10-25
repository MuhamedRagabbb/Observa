"""Multi-item checkout contracts (P1-A): inputs, attempt states and cart quotes.

Contact details are validated for the demo form but are not part of persisted or
telemetry-facing data.
"""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator

ATTEMPT_REF_PATTERN = re.compile(r"ATT-[0-9A-F]{4}-[0-9]{4}")


def is_attempt_ref(value):
    """Client-generated checkout attempt reference, also used as the Idempotency-Key."""
    return bool(ATTEMPT_REF_PATTERN.fullmatch(value))


class CheckoutItem(BaseModel):
    product_id: str = Field(pattern=r"^sku-[0-9]{3}$")
    quantity: int = Field(ge=1, le=10, strict=True)


class Contact(BaseModel):
    """Demo-only contact details: validated, then discarded (never stored or logged)."""

    name: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$", repr=False)
    email: str = Field(
        max_length=254, pattern=r"^[^@\s\x00-\x1f]+@[^@\s.\x00-\x1f]+(\.[^@\s.\x00-\x1f]+)+$", repr=False
    )


def merge_lines(items):
    """One line per product, sorted by product ID, at most 10 units each."""
    totals = {}
    for item in items:
        totals[item.product_id] = totals.get(item.product_id, 0) + item.quantity
    if max(totals.values()) > 10:
        raise ValueError("A product may not exceed 10 units per checkout")
    return [CheckoutItem(product_id=key, quantity=totals[key]) for key in sorted(totals)]


class CartLines(BaseModel):
    items: list[CheckoutItem] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def normalize(self):
        self.items = merge_lines(self.items)
        return self


class CheckoutInput(CartLines):
    customer_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    contact: Contact

    def request_hash(self):
        """Idempotency fingerprint of customer + normalized lines; contact data is excluded."""
        payload = {"customer_id": self.customer_id, "items": [[i.product_id, i.quantity] for i in self.items]}
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

    @property
    def total_units(self):
        return sum(item.quantity for item in self.items)


# Attempt state contract. Each saga step is persisted before the next remote call.
# An unknown dependency outcome never fails or releases directly: it is settled
# first (payment void fence, inventory release tombstone).
TRANSITIONS = {
    (None, "abandon"): "abandoned",
    ("received", "reserve"): "reserving",
    ("reserving", "reserved"): "reserved",
    ("reserving", "out_of_stock"): "not_placed",
    ("reserving", "reserve_unknown"): "releasing",
    ("reserved", "persist_failed"): "releasing",
    ("reserved", "charge"): "charging",
    ("charging", "charged"): "completing",
    ("charging", "declined"): "releasing_declined",
    ("charging", "charge_unknown"): "payment_unknown",
    ("payment_unknown", "voided"): "releasing_voided",
    ("payment_unknown", "found_succeeded"): "completing",
    ("payment_unknown", "found_declined"): "releasing_declined",
    ("completing", "persisted"): "completed",
    ("releasing", "released"): "failed",
    ("releasing_declined", "released"): "payment_declined",
    ("releasing_voided", "released"): "payment_failed",
}
TERMINAL_STATES = frozenset(
    {"completed", "payment_declined", "payment_failed", "not_placed", "failed", "abandoned"}
)
OUTCOMES = {
    "completed": ("confirmed", False),
    "payment_declined": ("payment_failed", True),
    "payment_failed": ("payment_failed", True),
    "not_placed": ("unavailable", False),
    "failed": ("failed", True),
    "abandoned": ("abandoned", True),
}


class InvalidTransition(ValueError):
    pass


def advance(state, event):
    try:
        return TRANSITIONS[(state, event)]
    except KeyError:
        raise InvalidTransition(f"{event!r} is not allowed from {state!r}") from None


def outcome(state):
    """Customer-facing outcome and whether a new attempt is safe; non-terminal states are processing."""
    return OUTCOMES.get(state, ("processing", False))


class QuoteLine(BaseModel):
    product_id: str
    name: str | None
    quantity: int
    unit_price_cents: int | None
    line_total_cents: int | None
    available: bool
    unavailable_reason: Literal["out_of_stock", "insufficient_stock", "unknown_product"] | None = None


class CartQuote(BaseModel):
    lines: list[QuoteLine]
    subtotal_cents: int
    unit_count: int
    unavailable_count: int
    can_checkout: bool


def build_quote(cart, products):
    """Price a cart against the current catalog without reserving stock."""
    catalog = {product["product_id"]: product for product in products}
    lines = []
    for item in cart.items:
        product = catalog.get(item.product_id)
        if product is None:
            lines.append(
                QuoteLine(
                    product_id=item.product_id,
                    name=None,
                    quantity=item.quantity,
                    unit_price_cents=None,
                    line_total_cents=None,
                    available=False,
                    unavailable_reason="unknown_product",
                )
            )
            continue
        reason = (
            "out_of_stock"
            if product["stock"] <= 0
            else "insufficient_stock"
            if product["stock"] < item.quantity
            else None
        )
        lines.append(
            QuoteLine(
                product_id=item.product_id,
                name=product["name"],
                quantity=item.quantity,
                unit_price_cents=product["price_cents"],
                line_total_cents=product["price_cents"] * item.quantity,
                available=reason is None,
                unavailable_reason=reason,
            )
        )
    available = [line for line in lines if line.available]
    return CartQuote(
        lines=lines,
        subtotal_cents=sum(line.line_total_cents for line in available),
        unit_count=sum(line.quantity for line in available),
        unavailable_count=len(lines) - len(available),
        can_checkout=len(available) == len(lines),
    )
