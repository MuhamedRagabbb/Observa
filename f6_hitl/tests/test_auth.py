"""F6-US-01 reviewer authorization: static hashed tokens, constant-time comparison."""
import hmac

import pytest

from observa_hitl import auth
from observa_hitl.auth import Reviewer, ReviewerDirectory

from hitl_helpers import write_reviewers


@pytest.fixture()
def directory(tmp_path):
    return ReviewerDirectory.from_file(write_reviewers(tmp_path / "reviewers.json"))


def test_valid_token_resolves_reviewer(directory):
    reviewer = directory.authenticate("alice-token")
    assert reviewer == Reviewer(reviewer_id="rev-alice", display_name="Alice (SRE)", roles={"reviewer", "viewer"})
    assert reviewer.can_review and reviewer.can_view
    viewer = directory.authenticate("vic-token")
    assert viewer.reviewer_id == "view-vic" and not viewer.can_review and viewer.can_view
    assert directory.authenticate("bob-token").can_view  # a reviewer may always read


def test_wrong_token_none_constant_time(directory, monkeypatch):
    calls = []
    real = hmac.compare_digest

    def counting(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", counting)
    assert directory.authenticate("not-a-token") is None
    assert directory.authenticate("") is None
    # Every stored hash is compared, whether or not an earlier one matched, so timing does not
    # reveal how many reviewers exist or which one nearly matched.
    calls.clear()
    assert directory.authenticate("alice-token").reviewer_id == "rev-alice"
    assert len(calls) == 3


def test_directory_rejects_plaintext_or_malformed_hashes(tmp_path):
    bad = tmp_path / "r.json"
    bad.write_text('{"reviewers": [{"reviewer_id": "x", "display_name": "X", "roles": ["reviewer"], '
                   '"token_sha256": "alice-token"}]}', encoding="utf-8")
    with pytest.raises(ValueError):
        ReviewerDirectory.from_file(bad)


def test_directory_rejects_duplicate_ids_and_unknown_roles(tmp_path):
    path = tmp_path / "r.json"
    h = "a" * 64
    path.write_text('{"reviewers": [{"reviewer_id": "x", "display_name": "X", "roles": ["admin"], '
                    f'"token_sha256": "{h}"}}]}}', encoding="utf-8")
    with pytest.raises(ValueError):
        ReviewerDirectory.from_file(path)
    path.write_text('{"reviewers": ['
                    f'{{"reviewer_id": "x", "display_name": "X", "roles": ["viewer"], "token_sha256": "{h}"}},'
                    f'{{"reviewer_id": "x", "display_name": "Y", "roles": ["viewer"], "token_sha256": "{"b" * 64}"}}'
                    ']}', encoding="utf-8")
    with pytest.raises(ValueError):
        ReviewerDirectory.from_file(path)


def test_lookup_by_id_for_sessions(directory):
    assert directory.get("rev-bob").display_name == "Bob (SRE)"
    assert directory.get("nobody") is None
