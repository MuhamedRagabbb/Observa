"""Deterministic synthetic observations, not captured production evidence."""
from .config import SERVICES
from .collect import queries


def snapshot(config, time, scenario=None):
    metrics = {key: {s: 0.0 for s in SERVICES} for key in queries(config)}
    for s in SERVICES:
        metrics["requests"][s] = 200
        metrics["payment_requests"][s] = 100
        metrics["dependency_requests"][s] = 100
        metrics["uptime"][s] = time
        metrics["latency"][s] = .1
        metrics["pool_pressure"][s] = .1
    result = {"time": time, "environment": config["environment"], "origin": "synthetic_demo",
              "metrics": metrics, "queries": queries(config), "log_query": "synthetic fixture; not a source capture",
              "logs": [], "traces": [], "source_health": {"prometheus": "ok", "loki": "ok", "jaeger": "ok"}}
    if not scenario:
        return result
    service = "payments-service" if scenario in ("payments", "declines") else "orders-service"
    if scenario in ("payments", "declines"):
        metrics["payment_failures"][service] = .85
    elif scenario == "pool":
        metrics["pool_pressure"][service] = .98
        metrics["pool_timeouts"][service] = 15
    elif scenario == "deployment":
        metrics["latency"][service] = 3
        for n in range(5):
            result["logs"].append({"service.name": service, "environment": config["environment"],
                                  "event": "order_persistence_failed", "error.type": "OrderPersistenceCompatibilityError",
                                  "level": "ERROR", "observed_at": time-n, "trace_id": f"{n+1:032x}"})
        result["logs"].append({"service.name": service, "environment": config["environment"], "event": "release_changed",
                              "level": "INFO", "service.version": "v2-example", "observed_at": time})
    else:
        raise ValueError("Unknown demo scenario")
    if scenario != "declines":
        for s in (service, "orders-service", "api-gateway"):
            metrics["http_errors"][s] = .7
        edges = [("api-gateway", "orders-service")]
        if service == "payments-service":
            edges.insert(0, ("orders-service", service))
        for upstream, downstream in edges:
            result["logs"].append({"service.name": upstream, "environment": config["environment"],
                                  "dependency": downstream, "event": "dependency_request_failed",
                                  "level": "ERROR", "http.status_code": 503, "observed_at": time, "trace_id": "a"*32})
        result["traces"].append({"trace_id": "a"*32, "spans": [
            {"service": s, "span_id": str(n), "operation": "synthetic-operation", "duration_us": 12000,
             "error": True, "http_status_code": 503, "references": []}
            for n, s in enumerate(dict.fromkeys((service, "orders-service", "api-gateway")))]})
    return result
