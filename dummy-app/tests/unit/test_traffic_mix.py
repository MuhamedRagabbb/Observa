"""Traffic mix (controllers/traffic_generator/mix.py): what the generator sends, decided without I/O."""

import json
import re
from collections import Counter
from pathlib import Path

from controllers.traffic_generator.mix import TrafficMix

LEGACY_SNAPSHOT = Path(__file__).parent / "data" / "legacy_traffic_seed42.json"


def drive(mix, count, respond):
    """Plan ``count`` requests, feeding each planned request's response back like the generator does."""
    sent = []
    for sequence in range(count):
        planned = mix.next(sequence)
        sent.append(planned)
        status, body = respond(planned)
        mix.observe(planned, status, body)
    return sent


def legacy_server():
    created = [0]

    def respond(planned):
        if planned.method == "POST" and planned.path == "/api/orders":
            created[0] += 1
            return 201, {"order_id": f"order-{created[0]}"}
        return 200, []

    return respond


def test_the_legacy_flow_sends_exactly_what_the_generator_sent_before():
    # Recorded from the unchanged generator (seed 42, one request at a time).
    expected = json.loads(LEGACY_SNAPSHOT.read_text(encoding="utf-8"))
    sent = drive(TrafficMix("legacy", seed=42), len(expected), legacy_server())
    assert [{"method": p.method, "path": p.path, "json": p.json} for p in sent] == expected


def checkout_server():
    """Accepts every checkout; enough for planning tests (the mesh tests use the real services)."""

    def respond(planned):
        if planned.path == "/api/checkout":
            return 201, {"attempt_ref": planned.headers.get("Idempotency-Key"), "state": "completed"}
        return 200, []

    return respond


def new_checkouts(sent):
    seen, fresh = set(), []
    for planned in sent:
        ref = planned.headers.get("Idempotency-Key")
        if planned.path == "/api/checkout" and ref and ref not in seen:
            seen.add(ref)
            fresh.append(planned)
    return fresh


def test_checkout_traffic_sends_varied_multi_item_carts_with_unique_attempt_references():
    sent = drive(TrafficMix("checkout", seed=7, run_prefix=0x1A2B), 2000, checkout_server())
    carts = [p for p in new_checkouts(sent) if kind(p) == "checkout"]

    assert len(carts) > 800
    for planned in carts:
        assert re.fullmatch(r"ATT-1A2B-[0-9]{4}", planned.headers["Idempotency-Key"])
        products = [line["product_id"] for line in planned.json["items"]]
        assert len(products) == len(set(products)) and set(products) <= {"sku-001", "sku-002", "sku-003"}
        assert all(1 <= line["quantity"] <= 3 for line in planned.json["items"])
        assert set(planned.json["contact"]) == {"name", "email"}
    assert {len(p.json["items"]) for p in carts} == {1, 2, 3}
    assert {sum(line["quantity"] for line in p.json["items"]) % 2 for p in carts} == {
        0,
        1,
    }  # F3 hits even totals


def test_checkout_traffic_never_touches_sku_005_which_the_live_tests_reserve_for_themselves():
    sent = drive(TrafficMix("checkout", seed=7, run_prefix=6), 3000, checkout_server())
    assert not any("sku-005" in str(p.json) for p in sent)


def test_the_same_seed_and_prefix_replay_the_same_traffic():
    first = drive(TrafficMix("checkout", seed=7, run_prefix=1), 500, checkout_server())
    again = drive(TrafficMix("checkout", seed=7, run_prefix=1), 500, checkout_server())
    other = drive(TrafficMix("checkout", seed=8, run_prefix=1), 500, checkout_server())
    assert first == again and first != other


def test_replays_resend_an_answered_checkout_exactly_and_conflicts_change_only_the_cart():
    sent = drive(TrafficMix("checkout", seed=7, run_prefix=2), 3000, checkout_server())
    first_by_ref, replays, conflicts = {}, 0, 0
    checkouts = [p for p in sent if p.path == "/api/checkout" and "Idempotency-Key" in p.headers]
    for index, planned in enumerate(checkouts):
        ref = planned.headers["Idempotency-Key"]
        first = first_by_ref.setdefault(ref, index)
        if first == index:
            continue
        original = checkouts[first]
        if planned == original:
            replays += 1
        else:
            conflicts += 1
            assert planned.json["customer_id"] == original.json["customer_id"]
            assert planned.json["items"] != original.json["items"]
    assert 90 <= replays <= 210 and 10 <= conflicts <= 60  # about 5% and 1% of 3000


def test_nothing_is_replayed_before_its_response_was_observed():
    mix = TrafficMix("checkout", seed=7, run_prefix=3)
    refs = [
        p.headers["Idempotency-Key"]
        for p in (mix.next(n) for n in range(1000))
        if "Idempotency-Key" in p.headers
    ]
    assert len(refs) == len(set(refs))


def kind(planned):
    body, headers = planned.json or {}, planned.headers
    if planned.path == "/api/checkout":
        products = {line["product_id"] for line in body["items"]}
        if "Idempotency-Key" not in headers:
            return "missing_key"
        if any(line["quantity"] == 0 for line in body["items"]):
            return "invalid_quantity"
        if "sku-999" in products:
            return "unknown_product"
        if "sku-004" in products:
            return "out_of_stock"
        if body["customer_id"].startswith("decline-"):
            return "decline"
        return "checkout"
    if planned.path.startswith("/api/attempts/"):
        return "attempt_lookup"
    if planned.path.startswith("/api/orders?customer_id="):
        return "my_orders"
    return {"/api/products": "products", "/api/cart/quote": "quote", "/unavailable": "not_found"}[
        planned.path
    ]


def test_checkout_traffic_keeps_the_noise_cases_at_their_planned_shares():
    sent = drive(TrafficMix("checkout", seed=11, run_prefix=4), 10000, checkout_server())
    shares = {name: count / len(sent) for name, count in Counter(kind(p) for p in sent).items()}
    planned = {  # replays and conflicts are counted under "checkout"
        "checkout": 0.61, "decline": 0.02, "out_of_stock": 0.02, "unknown_product": 0.01,
        "invalid_quantity": 0.01, "missing_key": 0.01, "attempt_lookup": 0.07, "my_orders": 0.05,
        "products": 0.15, "quote": 0.03, "not_found": 0.02,
    }  # fmt: skip
    assert shares.keys() == planned.keys()
    for name, share in planned.items():
        assert abs(shares[name] - share) < max(0.006, share * 0.2), (name, shares[name])


def test_lookups_ask_for_answered_attempts_with_their_own_customer():
    sent = drive(TrafficMix("checkout", seed=11, run_prefix=5), 3000, checkout_server())
    owners = {
        p.headers["Idempotency-Key"]: p.json["customer_id"] for p in sent if "Idempotency-Key" in p.headers
    }
    lookups = [p for p in sent if kind(p) == "attempt_lookup"]
    assert lookups
    for planned in lookups:
        match = re.fullmatch(r"/api/attempts/(ATT-[0-9A-F]{4}-[0-9]{4})\?customer_id=([\w-]+)", planned.path)
        assert match and owners[match[1]] == match[2]


def test_new_checkouts_do_not_depend_on_which_responses_have_arrived():
    """Under concurrency responses arrive in any order; fresh carts must stay a function of the seed."""
    answered = drive(TrafficMix("checkout", seed=9, run_prefix=7), 3000, checkout_server())
    silent = TrafficMix("checkout", seed=9, run_prefix=7)
    unanswered = [silent.next(n) for n in range(3000)]  # nothing observed yet: no replays or lookups
    fresh = {}
    for planned in new_checkouts(answered):
        fresh[planned.headers["Idempotency-Key"]] = planned.json
    for planned in new_checkouts(unanswered):
        assert fresh.pop(planned.headers["Idempotency-Key"]) == planned.json
    assert not fresh
    declines = sum(kind(p) == "decline" for p in unanswered) / len(unanswered)
    assert abs(declines - 0.02) < 0.006  # an empty history does not shift other shares
