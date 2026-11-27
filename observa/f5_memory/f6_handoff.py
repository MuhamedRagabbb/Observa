"""Feature 6 → Feature 5 write path: the memory side of F6's ``memory.approved_report.v1`` event.

Feature 6 (branch ``F6_HTL-Feedback`` at ``370df90``, 2026-11-25, no PR yet) defines a port,
``MemorySink.store(handoff: ApprovedReportHandoff) -> None``, that must be idempotent on
``handoff.decision_id``. F6 delivers through an outbox at least once, so the same handoff can
arrive again after a crash, and any exception counts as a failed delivery that F6 retries.

The handoff names the approved report version but does not contain it. Memory does not take
the handoff's word for anything: before saving it re-reads the decision from F6 and the exact
report version from F7, and checks the content against the handoff's ``content_digest``.

``IncidentMemorySink`` implements the port with the standard library only, so F6 can call it
in-process or the CLI can replay handoff files. ``HttpReviewServiceClient`` reads F6/F7's
review service API; ``FakeReviewService`` serves fake fixtures for tests and the demo.
"""

import copy
import json
from contextlib import contextmanager
from contextvars import ContextVar
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

from .service import IncidentMemory
from .validation import (DECISION_KEYS, REPORT_KEYS, MemoryOperationError, content_digest, digest_value,
                         integer, require, strings, text, timestamp)

MEMORY_EVENT = "memory.approved_report.v1"
HANDOFF_KEYS = {"decision_id", "incident_id", "report_id", "version", "content_ref", "content_digest",
                "approved_at", "reviewer_id", "evidence_ids"}
DEFAULT_REVIEW_URL = "http://127.0.0.1:8095"


def validate_handoff(payload):
    """Return a copy of an ``ApprovedReportHandoff`` payload (JSON form) after checking its shape."""
    if hasattr(payload, "model_dump"):  # F6's own pydantic model, when called in-process
        payload = payload.model_dump(mode="json")
    try:
        require(isinstance(payload, dict), "handoff must be an object")
        require(set(payload) == HANDOFF_KEYS, "handoff must have exactly the ApprovedReportHandoff keys")
        for key in ("decision_id", "incident_id", "report_id", "reviewer_id"):
            text(payload[key], f"handoff.{key}")
        integer(payload["version"], "handoff.version")
        digest_value(payload["content_digest"], "handoff.content_digest")
        require(payload["content_ref"] == f"sha256:{payload['content_digest']}", "handoff.content_ref must name its digest")
        timestamp(payload["approved_at"], "handoff.approved_at")
        strings(payload["evidence_ids"], "handoff.evidence_ids")
    except (KeyError, TypeError, AttributeError) as exc:
        raise MemoryOperationError("VALIDATION_ERROR", f"Malformed handoff: {exc}") from None
    return copy.deepcopy(payload)


class ReviewServiceClient(Protocol):
    def report_version(self, report_id: str, version: int) -> dict:
        """F7 ``GET /api/v1/reports/{id}/versions/{v}``: ``{"report_version": {...}, "content": {...}}``."""
        ...

    def review(self, report_id: str) -> dict:
        """F6 ``GET /api/v1/reports/{id}/review``: a ``ReviewSummary`` with ``decisions``."""
        ...


class HttpReviewServiceClient:
    """Read-only client for the F6/F7 review service. Sends GET requests only.

    ``token`` is a reviewer token with the ``viewer`` role. It is sent as a Bearer header and
    never logged or returned. Reads stay open on localhost when the service has no reviewers file.
    """

    def __init__(self, base_url=DEFAULT_REVIEW_URL, token=None, timeout=10.0):
        parsed = urllib.parse.urlsplit(base_url)
        require(parsed.scheme in ("http", "https") and parsed.netloc, "review service URL must be http(s)://host[:port]")
        self.base_url = base_url.rstrip("/")
        self._token = token
        self.timeout = timeout

    def _get(self, path):
        request = urllib.request.Request(self.base_url + path, method="GET", headers={"Accept": "application/json"})
        if self._token:
            request.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            message = _error_message(exc)
            if exc.code == 404:
                raise MemoryOperationError("NOT_FOUND", f"Review service: {message}") from None
            if exc.code in (401, 403):
                raise MemoryOperationError("NOT_AUTHORIZED", "Review service refused the read; check the viewer token") from None
            raise MemoryOperationError("SOURCE_UNAVAILABLE", f"Review service returned HTTP {exc.code}", exc.code >= 500) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise MemoryOperationError("SOURCE_UNAVAILABLE", "Review service is unreachable; retry later", True) from None
        try:
            return json.loads(body)
        except ValueError:
            raise MemoryOperationError("SOURCE_UNAVAILABLE", "Review service returned invalid JSON", True) from None

    def report_version(self, report_id, version):
        integer(version, "version")
        return self._get(f"/api/v1/reports/{urllib.parse.quote(report_id, safe='')}/versions/{version}")

    def review(self, report_id):
        return self._get(f"/api/v1/reports/{urllib.parse.quote(report_id, safe='')}/review")


def _error_message(exc):
    try:
        return json.loads(exc.read())["message"]
    except (ValueError, KeyError, TypeError, OSError):
        return f"HTTP {exc.code}"
    finally:
        exc.close()


class FakeReviewService:
    """Local test/demo data ONLY: serves F7 documents and F6 decisions from fake snapshots."""

    def __init__(self, snapshots, review_status=None):
        self._documents, self._reviews = {}, {}
        for snapshot in copy.deepcopy(snapshots):
            report, decision = snapshot["report"], snapshot["decision"]
            status = "approved" if decision["decision"] == "approve" else "changes_requested"
            if review_status is not None:
                status = review_status
            version = {k: v for k, v in report.items() if k != "content"}
            self._documents[(report["report_id"], report["version"])] = {
                "report_version": {**version, "review_status": status}, "content": report["content"]}
            summary = self._reviews.setdefault(report["report_id"], {"report_id": report["report_id"], "decisions": []})
            summary["decisions"].append({**decision, "review_cycle_id": f"CYCLE-{report['report_id']}-{report['version']}",
                                         "memory_sync_status": "pending"})
        self.calls = 0
        self.fail_next = 0

    def _maybe_fail(self):
        self.calls += 1
        if self.fail_next:
            self.fail_next -= 1
            raise MemoryOperationError("SOURCE_UNAVAILABLE", "Fake review service outage", True)

    def report_version(self, report_id, version):
        self._maybe_fail()
        if (report_id, version) not in self._documents:
            raise MemoryOperationError("NOT_FOUND", f"{report_id} v{version} does not exist")
        return copy.deepcopy(self._documents[(report_id, version)])

    def review(self, report_id):
        self._maybe_fail()
        if report_id not in self._reviews:
            raise MemoryOperationError("NOT_FOUND", f"Report {report_id} does not exist")
        return copy.deepcopy(self._reviews[report_id])

    def document(self, report_id, version):
        """Direct access for tests that tamper with what the service returns."""
        return self._documents[(report_id, version)]

    def decisions(self, report_id):
        return self._reviews[report_id]["decisions"]


def handoff_for(snapshot):
    """The handoff F6 would send for a fake snapshot's approval (tests and the demo)."""
    report, decision = snapshot["report"], snapshot["decision"]
    return {"decision_id": decision["decision_id"], "incident_id": report["incident_id"],
            "report_id": report["report_id"], "version": report["version"], "content_ref": report["content_ref"],
            "content_digest": report["content_digest"], "approved_at": decision["decided_at"],
            "reviewer_id": decision["reviewer_id"], "evidence_ids": list(report["evidence_ids"])}


class F6ApprovalSource:
    """Trusted approval source for handoffs from F6. A handoff alone is never trusted.

    ``resolve`` re-reads the decision from F6 and the report version from F7, and requires
    both to agree with the handoff. Authorization of the reviewer stays with F6.
    """

    def __init__(self, client: ReviewServiceClient):
        self._client = client
        self._handoff = ContextVar("approval_handoff", default=None)

    @contextmanager
    def delivery(self, handoff):
        """Bind the payload to this save attempt; SQLite retains committed decisions."""
        handoff = validate_handoff(handoff)
        token = self._handoff.set(handoff)
        try:
            yield handoff["decision_id"]
        finally:
            self._handoff.reset(token)

    def resolve(self, decision_id):
        handoff = self._handoff.get()
        require(handoff is not None and handoff["decision_id"] == decision_id, "No F6 handoff received for this decision", "NOT_FOUND")
        report_id, version = handoff["report_id"], handoff["version"]
        try:
            decision = self._decision(handoff)
            document = self._client.report_version(report_id, version)
            require(isinstance(document, dict), "report document must be an object")
            record, content = document["report_version"], document["content"]
            require(isinstance(record, dict), "report_version must be an object")
            for key in ("report_id", "incident_id", "version", "content_ref", "content_digest", "evidence_ids"):
                require(record[key] == handoff[key], f"F7 {key} differs from the handoff", "VERSION_CONFLICT")
            require(content_digest(content) == handoff["content_digest"],
                    "F7 content does not match the approved digest", "VERSION_CONFLICT")
            require(record["review_status"] == "approved", "F7 does not show this version as approved", "NOT_APPROVED")
            report = {key: copy.deepcopy(record[key]) for key in REPORT_KEYS - {"content"}}
        except (KeyError, TypeError) as exc:
            raise MemoryOperationError("VALIDATION_ERROR", f"Malformed review service response: {exc}") from None
        report["content"] = content
        return {"report": report, "decision": decision}

    def _decision(self, handoff):
        review = self._client.review(handoff["report_id"])
        require(isinstance(review, dict) and isinstance(review.get("decisions"), list), "review must list decisions")
        matches = [d for d in review["decisions"] if isinstance(d, dict) and d.get("decision_id") == handoff["decision_id"]]
        require(len(matches) == 1, "F6 has no such decision for this report", "NOT_FOUND")
        found = matches[0]
        decision = {key: copy.deepcopy(found[key]) for key in DECISION_KEYS}
        for key in ("incident_id", "report_id", "version", "reviewer_id"):
            require(decision[key] == handoff[key], f"F6 decision {key} differs from the handoff", "VERSION_CONFLICT")
        require(timestamp(decision["decided_at"], "decided_at") == timestamp(handoff["approved_at"], "approved_at"),
                "F6 decision time differs from the handoff", "VERSION_CONFLICT")
        return decision


class IncidentMemorySink:
    """F5's implementation of F6's ``MemorySink`` port.

    ``store`` returns None when the approval is stored (or was already stored with the same
    content) and raises ``MemoryOperationError`` otherwise, so F6's outbox records the failure
    and retries. ``NOT_APPROVED``, ``VERSION_CONFLICT`` and ``VALIDATION_ERROR`` will not succeed
    on retry; ``STORAGE_FAILURE`` and ``SOURCE_UNAVAILABLE`` may.
    """

    def __init__(self, memory: IncidentMemory, source: F6ApprovalSource):
        require(memory.approval_source is source, "memory must resolve approvals through this sink's source", "NOT_AUTHORIZED")
        self.memory = memory
        self.source = source

    @classmethod
    def connect(cls, database, client: ReviewServiceClient):
        source = F6ApprovalSource(client)
        return cls(IncidentMemory(database, source), source)

    def store(self, handoff):
        self.save(handoff)

    def store_payload(self, payload):
        self.save(payload)

    def save(self, handoff):
        """Like ``store``, but returns memory's save result (``status``, ``created``, ``item``)."""
        with self.source.delivery(handoff) as decision_id:
            return self.memory.save_approved_incident(decision_id)
