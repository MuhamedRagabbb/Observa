"""Typed F7 errors carrying the contract ErrorEnvelope (contract invariant 7)."""
from observa_investigation.contracts import ErrorEnvelope


class ReportError(Exception):
    def __init__(self, code: str, message: str, *, operation_id: str, retryable: bool = False,
                 details: dict | None = None):
        self.envelope = ErrorEnvelope(code=code, message=message, retryable=retryable,
                                      operation_id=operation_id, details=details)
        super().__init__(f"[{code}] {message}")
