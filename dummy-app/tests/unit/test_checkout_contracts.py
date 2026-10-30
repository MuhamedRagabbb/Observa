import pytest
from pydantic import ValidationError

from services.shared.checkout import CheckoutInput, is_attempt_ref

CONTACT = {"name": "Demo name", "email": "demo@example.com"}


ONE_ITEM = [{"product_id": "sku-001", "quantity": 1}]


def checkout(items=ONE_ITEM, **overrides):
    return CheckoutInput(**{"customer_id": "web-user-1", "contact": CONTACT, "items": items, **overrides})


def test_items_are_merged_by_product_and_sorted():
    data = checkout(
        [
            {"product_id": "sku-003", "quantity": 1},
            {"product_id": "sku-001", "quantity": 2},
            {"product_id": "sku-003", "quantity": 4},
        ]
    )
    assert [(item.product_id, item.quantity) for item in data.items] == [("sku-001", 2), ("sku-003", 5)]
    assert data.total_units == 7


def test_request_hash_identifies_the_order_not_the_contact():
    base = checkout([{"product_id": "sku-001", "quantity": 3}, {"product_id": "sku-002", "quantity": 1}])
    reordered_and_split = checkout(
        [
            {"product_id": "sku-002", "quantity": 1},
            {"product_id": "sku-001", "quantity": 1},
            {"product_id": "sku-001", "quantity": 2},
        ],
        contact={"name": "Someone else", "email": "other@example.com"},
    )
    assert base.request_hash() == reordered_and_split.request_hash()
    assert len(base.request_hash()) == 64
    assert base.request_hash() != checkout([{"product_id": "sku-001", "quantity": 3}]).request_hash()
    assert (
        base.request_hash() != checkout(base.model_dump()["items"], customer_id="web-user-2").request_hash()
    )
    assert "demo@example.com" not in repr(base) and "Demo name" not in repr(base)


@pytest.mark.parametrize(
    "overrides",
    [
        {"items": []},
        {"items": [{"product_id": f"sku-{n:03d}", "quantity": 1} for n in range(1, 12)]},
        {"items": [{"product_id": "sku-001", "quantity": 6}, {"product_id": "sku-001", "quantity": 5}]},
        {"items": [{"product_id": "sku-001", "quantity": "2"}]},
        {"items": [{"product_id": "bad", "quantity": 1}]},
        {"customer_id": "bad\nvalue"},
        {"contact": {"name": "", "email": "demo@example.com"}},
        {"contact": {"name": "Demo\x00name", "email": "demo@example.com"}},
        {"contact": {"name": "x" * 81, "email": "demo@example.com"}},
        {"contact": {"name": "Demo name", "email": "not-an-email"}},
        {"contact": {"name": "Demo name", "email": "a@b"}},
    ],
)
def test_invalid_checkouts_are_rejected(overrides):
    with pytest.raises(ValidationError):
        checkout(**overrides)


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("ATT-7F3A-0915", True),
        ("ATT-0000-0000", True),
        ("att-7f3a-0915", False),
        ("ATT-7F3A-091", False),
        ("ATT-7G3A-0915", False),
        ("OBS-1042-7F3A", False),
        ("ATT-7F3A-0915\n", False),
        ("", False),
    ],
)
def test_attempt_reference_format(value, valid):
    assert is_attempt_ref(value) is valid
