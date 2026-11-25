"""Typed F6 errors carrying the contract ErrorEnvelope (contract invariant 7)."""
from observa_investigation.contracts import ErrorEnvelope

# Contract codes plus the F6-specific IDEMPOTENCY_CONFLICT and NOT_FOUND.
CODES = ("VALIDATION_ERROR", "NOT_AUTHORIZED", "VERSION_CONFLICT", "STORAGE_FAILURE", "RETRY_LIMIT_REACHED",
         "IDEMPOTENCY_CONFLICT", "NOT_FOUND")


class F6Error(Exception):
    def __init__(self, code: str, message: str, *, operation_id: str, retryable: bool = False,
                 details: dict | None = None):
        if code not in CODES:
            raise ValueError(f"unknown F6 error code {code!r}")
        self.envelope = ErrorEnvelope(code=code, message=message, retryable=retryable,
                                      operation_id=operation_id, details=details)
        super().__init__(f"[{code}] {message}")
