"""What the traffic generator sends next, decided without I/O so it can be tested and replayed.

``TrafficMix.next(sequence)`` plans one request; ``observe`` feeds the response back, so lookups
and replays can target requests that really were answered.

Determinism: the legacy mix keeps its original single seeded stream. In the checkout mix every
choice and cart is a function of (seed, sequence) alone, so response timing never changes what a
sequence sends; only which answered checkout a replay or lookup picks depends on response order.
"""

import random
import secrets
from collections import deque
from dataclasses import dataclass, field

FLOWS = ("legacy", "checkout")
# Same products as the legacy mix: sku-005 stays free of background traffic for the live tests.
IN_STOCK = ("sku-001", "sku-002", "sku-003")
# Demo contact: validated by the Gateway, then dropped (never stored, logged or traced).
CONTACT = {"name": "Load Test", "email": "load-test@example.com"}
# Outcomes that a retry must reproduce exactly; 5xx and 202 may legitimately change on a retry.
DEFINITE = frozenset({201, 402, 409})
# Checkout mix: (upper bound of the random choice, planner). Shares are documented in the README.
BANDS = (
    (0.55, "_fresh"),  # multi-item cart
    (0.60, "_replay"),  # same key, same body: same result, no second charge
    (0.61, "_conflict"),  # same key, changed cart: 409 idempotency_conflict
    (0.63, "_decline"),  # 402
    (0.65, "_out_of_stock"),  # 409 not placed (sku-004)
    (0.66, "_unknown_product"),
    (0.67, "_zero_quantity"),  # 422
    (0.68, "_missing_key"),  # 422
    (0.75, "_lookup"),  # GET /api/attempts/{ref}
    (0.80, "_my_orders"),
    (0.95, "_products"),
    (0.98, "_quote"),
    (1.00, "_not_found"),
)


@dataclass(frozen=True)
class Planned:
    method: str
    path: str
    json: dict | None = None
    headers: dict = field(default_factory=dict)


class TrafficMix:
    def __init__(self, flow, *, seed, run_prefix=None):
        """``run_prefix`` (0-65535) starts this run's attempt references; random when omitted, so a
        restarted generator does not replay the previous run's references."""
        if flow not in FLOWS:
            raise ValueError(f"Unknown traffic flow {flow!r}")
        self.flow, self.seed, self.rng = flow, seed, random.Random(seed)
        self.run_prefix = secrets.randbelow(0x10000) if run_prefix is None else run_prefix
        self.orders = deque(maxlen=500)
        self.attempts_started = 0
        self.answered, self.answered_refs = deque(maxlen=500), set()  # checkouts with a definite result

    def next(self, sequence):
        if self.flow == "legacy":
            return self._legacy(sequence, self.rng.random())
        rng = random.Random(f"{self.seed}:{sequence}")
        choice = rng.random()
        planner = next(name for bound, name in BANDS if choice < bound)
        return getattr(self, planner)(rng, sequence) or self._products(rng, sequence)

    def observe(self, planned, status, body):
        if planned.method == "POST" and planned.path == "/api/orders" and status == 201:
            self.orders.append(body["order_id"])
        ref = planned.headers.get("Idempotency-Key")
        if planned.path == "/api/checkout" and ref and ref not in self.answered_refs and status in DEFINITE:
            self.answered_refs.add(ref)
            self.answered.append(planned)

    # Checkout-mix planners: each returns a request, or None to fall back to a catalog read.

    def _fresh(self, rng, sequence):
        return self._checkout(self._customer(sequence), self._cart(rng))

    def _replay(self, rng, sequence):
        # Only answered checkouts are replayed, so a replay never races its original.
        return self._answered(sequence)

    def _conflict(self, rng, sequence):
        original = self._answered(sequence)
        if original is None:
            return None
        items = [dict(line) for line in original.json["items"]]
        items[0]["quantity"] = items[0]["quantity"] % 3 + 1
        return Planned("POST", "/api/checkout", {**original.json, "items": items}, original.headers)

    def _decline(self, rng, sequence):
        return self._checkout(f"decline-{sequence}", self._cart(rng))

    def _out_of_stock(self, rng, sequence):
        items = [*self._cart(rng), {"product_id": "sku-004", "quantity": 1}]
        return self._checkout(self._customer(sequence), sorted(items, key=lambda line: line["product_id"]))

    def _unknown_product(self, rng, sequence):
        return self._checkout(
            self._customer(sequence), [*self._cart(rng), {"product_id": "sku-999", "quantity": 1}]
        )

    def _zero_quantity(self, rng, sequence):
        first, *rest = self._cart(rng)
        return self._checkout(self._customer(sequence), [{**first, "quantity": 0}, *rest])

    def _missing_key(self, rng, sequence):
        return Planned("POST", "/api/checkout", self._body(self._customer(sequence), self._cart(rng)))

    def _lookup(self, rng, sequence):
        original = self._answered(sequence)
        if original is None:
            return None
        ref, owner = original.headers["Idempotency-Key"], original.json["customer_id"]
        return Planned("GET", f"/api/attempts/{ref}?customer_id={owner}")

    def _my_orders(self, rng, sequence):
        return Planned("GET", f"/api/orders?customer_id={self._customer(sequence)}")

    def _products(self, rng, sequence):
        return Planned("GET", "/api/products")

    def _quote(self, rng, sequence):
        return Planned("POST", "/api/cart/quote", {"items": self._cart(rng)})

    def _not_found(self, rng, sequence):
        return Planned("GET", "/unavailable")

    # Helpers

    @staticmethod
    def _customer(sequence):
        return f"load-user-{sequence % 50}"

    @staticmethod
    def _cart(rng):
        products = rng.sample(IN_STOCK, rng.randint(1, 3))
        return [{"product_id": product, "quantity": rng.randint(1, 3)} for product in sorted(products)]

    @staticmethod
    def _body(customer, items):
        return {"customer_id": customer, "contact": CONTACT, "items": items}

    def _answered(self, sequence):
        return self.answered[sequence % len(self.answered)] if self.answered else None

    def _checkout(self, customer, items):
        number = self.attempts_started
        self.attempts_started += 1
        prefix = (self.run_prefix + number // 10000) % 0x10000
        ref = f"ATT-{prefix:04X}-{number % 10000:04d}"
        return Planned("POST", "/api/checkout", self._body(customer, items), {"Idempotency-Key": ref})

    def _legacy(self, sequence, choice):
        """The original single-item mix: orders with negative cases, catalog, lookups, a 404."""
        if choice < 0.65:
            product = (
                "sku-999" if choice < 0.02 else "sku-004" if choice < 0.04 else f"sku-{sequence % 3 + 1:03d}"
            )
            quantity = 0 if 0.04 <= choice < 0.06 else sequence % 3 + 1
            customer = f"decline-{sequence}" if 0.06 <= choice < 0.08 else f"load-user-{sequence % 50}"
            return Planned(
                "POST", "/api/orders", {"product_id": product, "quantity": quantity, "customer_id": customer}
            )
        if choice < 0.85:
            return Planned("GET", "/api/products")
        if choice < 0.98 and self.orders:
            return Planned("GET", "/api/orders/" + self.orders[sequence % len(self.orders)])
        return Planned("GET", "/unavailable")
