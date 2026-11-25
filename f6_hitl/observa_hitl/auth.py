"""Reviewer identities from a static file of sha256 token hashes (F6-US-01 authorization).

The file (review_service/reviewers.json, git-ignored) never holds a plaintext token:

    {"reviewers": [{"reviewer_id": "...", "display_name": "...", "roles": ["reviewer", "viewer"],
                    "token_sha256": "<64 hex chars>"}]}

A second adapter (OIDC) can replace this class later behind the same two methods.
"""
import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Role = Literal["reviewer", "viewer"]
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Reviewer(BaseModel):
    model_config = ConfigDict(frozen=True)
    reviewer_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    roles: frozenset[Role] = Field(min_length=1)

    @property
    def can_review(self) -> bool:
        return "reviewer" in self.roles

    @property
    def can_view(self) -> bool:
        return bool(self.roles)  # a reviewer may always read what they review


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    roles: list[Role] = Field(min_length=1)
    token_sha256: str

    @field_validator("token_sha256")
    @classmethod
    def _hex(cls, value: str) -> str:
        if not _HEX64.match(value):
            raise ValueError("token_sha256 must be a lowercase sha256 hex digest, never a plaintext token")
        return value


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewers: list[_Entry]


class ReviewerDirectory:
    def __init__(self, entries: list[_Entry]):
        ids = [e.reviewer_id for e in entries]
        if len(ids) != len(set(ids)):
            raise ValueError("reviewer_id values must be unique")
        hashes = [e.token_sha256 for e in entries]
        if len(hashes) != len(set(hashes)):
            raise ValueError("token hashes must be unique")
        self._entries = [(e.token_sha256.encode(), Reviewer(reviewer_id=e.reviewer_id, display_name=e.display_name,
                                                             roles=frozenset(e.roles))) for e in entries]
        self._by_id = {r.reviewer_id: r for _, r in self._entries}

    @classmethod
    def from_file(cls, path: str | Path) -> "ReviewerDirectory":
        return cls(_File.model_validate(json.loads(Path(path).read_text(encoding="utf-8"))).reviewers)

    def authenticate(self, token: str) -> Reviewer | None:
        presented = hashlib.sha256((token or "").encode()).hexdigest().encode()
        found = None
        for stored, reviewer in self._entries:  # compare against every entry: no early exit
            if hmac.compare_digest(stored, presented) and token:
                found = reviewer
        return found

    def get(self, reviewer_id: str) -> Reviewer | None:
        return self._by_id.get(reviewer_id)
