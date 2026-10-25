import pytest

from services.shared.checkout import TERMINAL_STATES, InvalidTransition, advance, outcome


def walk(*events, start="received"):
    state = start
    for event in events:
        state = advance(state, event)
    return state


@pytest.mark.parametrize(
    ("events", "final"),
    [
        (("reserve", "reserved", "charge", "charged", "persisted"), "completed"),
        (("reserve", "out_of_stock"), "not_placed"),
        (("reserve", "reserve_unknown", "released"), "failed"),
        (("reserve", "reserved", "persist_failed", "released"), "failed"),
        (("reserve", "reserved", "charge", "declined", "released"), "payment_declined"),
        (("reserve", "reserved", "charge", "charge_unknown", "voided", "released"), "payment_failed"),
        (("reserve", "reserved", "charge", "charge_unknown", "found_succeeded", "persisted"), "completed"),
        (
            ("reserve", "reserved", "charge", "charge_unknown", "found_declined", "released"),
            "payment_declined",
        ),
    ],
)
def test_checkout_paths_reach_their_outcome(events, final):
    assert walk(*events) == final


def test_absent_attempt_can_only_be_fenced_as_abandoned():
    assert advance(None, "abandon") == "abandoned"
    with pytest.raises(InvalidTransition):
        advance(None, "reserve")


@pytest.mark.parametrize(
    ("state", "event"),
    [
        # Unknown payment never releases stock or fails directly; it must be settled first.
        ("payment_unknown", "released"),
        ("payment_unknown", "persisted"),
        # A charged payment must complete, never be released.
        ("completing", "released"),
        ("charging", "released"),
        ("received", "charged"),
    ],
)
def test_unsafe_shortcuts_are_rejected(state, event):
    with pytest.raises(InvalidTransition):
        advance(state, event)


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
def test_terminal_states_are_immutable(state):
    for event in ("reserve", "released", "persisted", "charged", "abandon", "voided"):
        with pytest.raises(InvalidTransition):
            advance(state, event)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("completed", ("confirmed", False)),
        ("payment_declined", ("payment_failed", True)),
        ("payment_failed", ("payment_failed", True)),
        ("not_placed", ("unavailable", False)),
        ("failed", ("failed", True)),
        ("abandoned", ("abandoned", True)),
        ("payment_unknown", ("processing", False)),
        ("received", ("processing", False)),
    ],
)
def test_states_map_to_customer_outcomes(state, expected):
    assert outcome(state) == expected
