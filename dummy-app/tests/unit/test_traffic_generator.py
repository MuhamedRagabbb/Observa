"""The traffic generator end to end against the in-memory mesh (Gateway, Orders, Inventory, Payments)."""

from collections import Counter

from controllers.traffic_generator.main import run
from services.shared.checkout import TERMINAL_STATES


def env(**overrides):
    return {
        "TRAFFIC_RPS": "50",
        "TRAFFIC_CONCURRENCY": "8",
        "TRAFFIC_SEED": "42",
        "GATEWAY_URL": "http://gateway",
        **overrides,
    }


async def test_the_default_flow_is_the_legacy_single_item_traffic(mesh):
    counts = await run(env(), transport=mesh["transport"], max_requests=60)

    assert counts["201"] > 0 and "transport_error" not in counts
    paths = {path for method, host, path, _ in mesh["transport"].requests if host == "gateway"}
    assert "/api/orders" in paths and "/api/checkout" not in paths


async def test_checkout_traffic_completes_carts_once_and_answers_every_noise_case(mesh):
    inventory = mesh["apps"]["inventory"].state.repository
    inventory.stock.update({"sku-003": 1000, "sku-005": 1000})  # the live catalog's in-stock products
    before = dict(inventory.stock)

    counts = await run(
        env(TRAFFIC_FLOW="checkout", TRAFFIC_RUN_PREFIX="4660"), transport=mesh["transport"], max_requests=400
    )

    assert {"200", "201", "402", "404", "409", "422"} <= counts.keys(), counts
    assert not any(status.startswith("5") for status in counts) and "transport_error" not in counts
    attempts = list(mesh["apps"]["orders"].state.attempts.rows.values())
    completed = [row for row in attempts if row["state"] == "completed"]
    assert all(row["attempt_ref"].startswith("ATT-1234-") for row in attempts)  # TRAFFIC_RUN_PREFIX
    assert completed and all(row["state"] in TERMINAL_STATES for row in attempts)
    charges = [record for _, record, _ in mesh["apps"]["payments"].state.repository.rows.values()]
    assert sum(record["status"] == "succeeded" for record in charges) == len(
        completed
    )  # replays never recharge
    sold = Counter()
    for row in completed:
        for line in row["lines"]:
            sold[line["product_id"]] += line["quantity"]
    assert {sku: before[sku] - inventory.stock[sku] for sku in before} == {sku: sold[sku] for sku in before}
