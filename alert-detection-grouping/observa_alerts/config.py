import json
import os
from pathlib import Path

SERVICES = ("api-gateway", "orders-service", "inventory-service", "payments-service")

DEFAULTS = {
    "environment": "local", "poll_seconds": 10, "window_seconds": 60,
    "pending_seconds": 20, "recovery_seconds": 60, "group_window_seconds": 120,
    "freshness_seconds": 30, "min_requests": 10, "log_limit": 1000, "trace_limit": 12,
    "thresholds": {"http_errors": .15, "latency": 2.0, "pool_pressure": .9,
                   "pool_timeouts": 2, "payment_failures": .25,
                   "dependency_errors": .15, "new_exception": 3, "persistence_errors": 2},
}


def settings(path=None):
    result = json.loads(json.dumps(DEFAULTS))
    if path:
        supplied = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = set(supplied) - set(result)
        if unknown:
            raise ValueError(f"Unknown settings: {sorted(unknown)}")
        result["thresholds"].update(supplied.pop("thresholds", {}))
        result.update(supplied)
    for key in ("poll_seconds", "window_seconds", "recovery_seconds", "group_window_seconds",
                "freshness_seconds", "min_requests", "log_limit", "trace_limit"):
        if not isinstance(result[key], (int, float)) or result[key] <= 0:
            raise ValueError(f"{key} must be positive")
    if result["pending_seconds"] < 0 or any(v <= 0 for v in result["thresholds"].values()):
        raise ValueError("Invalid thresholds or pending time")
    if not str(result["environment"]).replace("-", "").replace("_", "").isalnum():
        raise ValueError("Use an alphanumeric environment name")
    result["urls"] = {
        name: os.getenv(name.upper() + "_URL", default).rstrip("/")
        for name, default in (("prometheus", "http://127.0.0.1:9090"),
                              ("loki", "http://127.0.0.1:3100"),
                              ("jaeger", "http://127.0.0.1:16686"))
    }
    return result
