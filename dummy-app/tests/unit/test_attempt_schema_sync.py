"""The orders migration's state CHECK must list exactly the states of the attempt contract."""

import re
from pathlib import Path

from services.shared.checkout import TERMINAL_STATES, TRANSITIONS

MIGRATION = (
    Path(__file__).resolve().parents[2] / "scripts" / "migrations" / "orders" / "001_checkout_attempts.sql"
)


def test_migration_state_list_matches_the_state_contract():
    sql = MIGRATION.read_text(encoding="utf-8")
    listed = set(re.findall(r"'([a-z_]+)'", re.search(r"state IN \((.*?)\)\)", sql, re.S).group(1)))
    contract = {state for (state, _), target in TRANSITIONS.items() for state in (state, target) if state}
    assert listed == contract and TERMINAL_STATES <= listed
