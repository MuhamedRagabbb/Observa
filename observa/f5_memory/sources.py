"""Approval-source port and development-only fake implementation."""

import copy
import json
from pathlib import Path
from typing import Protocol

from .validation import MemoryOperationError, require


class TrustedApprovalSource(Protocol):
    def resolve(self, decision_id: str) -> dict:
        """Return exact immutable report + incident + durable F6 decision snapshot.

        A production adapter must verify reviewer authorization, decision authenticity,
        and actionable report version at decision time. A caller-supplied approved
        flag is not a trusted source. Authorization belongs to F6, not this port.
        """
        ...


class FakeApprovalSource:
    """Local test/demo data ONLY. Never connect this implementation to public writes."""

    def __init__(self, snapshots):
        self._snapshots = copy.deepcopy(snapshots)
        ids = [item["decision"]["decision_id"] for item in self._snapshots]
        require(len(ids) == len(set(ids)), "Duplicate fixture decision IDs")

    @classmethod
    def from_file(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def resolve(self, decision_id):
        for snapshot in self._snapshots:
            if snapshot["decision"]["decision_id"] == decision_id:
                return copy.deepcopy(snapshot)
        raise MemoryOperationError("NOT_FOUND", "Approval decision not found")

    def decision_ids(self):
        return [item["decision"]["decision_id"] for item in self._snapshots]
