"""F5 (Incident Knowledge Memory) mocks behind F6's MemorySink port.

Both are idempotent on decision_id, as the contract requires of F5: redelivering the same
approval never creates a second knowledge record.
"""
import json
import os
from pathlib import Path

from observa_hitl.ports import ApprovedReportHandoff


class MemoryOutage(RuntimeError):
    """F5 is unreachable (injected)."""


class FakeMemorySink:
    """In-memory F5 with failure injection, for tests.

    fail_times:        the first N calls fail before storing anything
    fail_forever:      every call fails before storing anything
    crash_after_store: the first call stores the record and then fails, as if the relay died
                       after F5 committed but before the outbox event was acknowledged
    """

    def __init__(self, *, fail_times: int = 0, fail_forever: bool = False, crash_after_store: bool = False):
        self.fail_times, self.fail_forever, self.crash_after_store = fail_times, fail_forever, crash_after_store
        self.records: dict[str, ApprovedReportHandoff] = {}
        self.calls = 0

    def store(self, handoff: ApprovedReportHandoff) -> None:
        self.calls += 1
        if self.fail_forever or self.fail_times > 0:
            self.fail_times = max(0, self.fail_times - 1)
            raise MemoryOutage("injected F5 outage")
        existing = self.records.get(handoff.decision_id)
        if existing is not None and existing != handoff:
            raise ValueError(f"decision {handoff.decision_id} was already stored with different content")
        self.records[handoff.decision_id] = handoff
        if self.crash_after_store:
            self.crash_after_store = False
            raise MemoryOutage("injected crash after F5 stored the record")

    def store_payload(self, payload: dict) -> None:
        self.store(ApprovedReportHandoff.model_validate(payload))


class FileMemorySink:
    """One JSON file per approved decision under a directory: a durable local stand-in for F5."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)

    def store(self, handoff: ApprovedReportHandoff) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{handoff.decision_id}.json"
        body = json.dumps(handoff.model_dump(mode="json"), sort_keys=True, indent=2)
        if target.exists():
            if target.read_text(encoding="utf-8") != body:
                raise ValueError(f"decision {handoff.decision_id} was already stored with different content")
            return
        temp = target.with_suffix(".json.tmp")
        temp.write_text(body, encoding="utf-8")
        os.replace(temp, target)  # atomic: a crash never leaves a half-written record
