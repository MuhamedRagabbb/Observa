"""Service health for the operator (controllers/fault_injector/health.py): real probes plus Prometheus.

Never derived from fault state. Each test fakes the probes and Prometheus at the HTTP boundary.
"""

import httpx

from controllers.fault_injector.health import HealthMonitor

SERVICES = {
    "api-gateway": "http://gateway:8000",
    "orders-service": "http://orders:8001",
}
PROMETHEUS = "http://prometheus:9090"


def prometheus(rates):
    """Fake Prometheus: ``rates`` maps service -> (requests/s, 5xx/s, seconds/s, observations/s)."""

    def answer(query):
        index = (
            1
            if 'http_status_code=~"5.."' in query
            else 2
            if "duration_seconds_sum" in query
            else 3
            if "duration_seconds_count" in query
            else 0
        )
        return [
            {"metric": {"service_name": service}, "value": [0, str(values[index])]}
            for service, values in rates.items()
        ]

    return answer


def transport(*, probes=None, metrics=None):
    probes = probes or {}

    def handle(request):
        if request.url.host == "prometheus":
            if metrics is None:
                raise httpx.ConnectError("prometheus down")
            if isinstance(metrics, int):
                return httpx.Response(metrics, json={"status": "error"})
            result = metrics(request.url.params["query"])
            return httpx.Response(
                200, json={"status": "success", "data": {"resultType": "vector", "result": result}}
            )
        outcome = probes.get(request.url.host, 200)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, json={"status": "ready" if outcome == 200 else "unavailable"})

    return httpx.MockTransport(handle)


def monitor(**kwargs):
    return HealthMonitor(httpx.AsyncClient(transport=transport(**kwargs)), SERVICES, PROMETHEUS)


def states(snapshot):
    return {row["service"]: row["state"] for row in snapshot["services"]}


async def test_a_ready_service_with_few_5xx_and_normal_latency_is_healthy_otherwise_degraded():
    rates = {
        "api-gateway": (10, 0.1, 0.5, 10),  # 1% 5xx, mean 50 ms
        "orders-service": (10, 2, 0.5, 10),  # 20% 5xx
    }
    snapshot = await monitor(metrics=prometheus(rates)).check()

    assert states(snapshot) == {"api-gateway": "healthy", "orders-service": "degraded"}
    gateway = next(row for row in snapshot["services"] if row["service"] == "api-gateway")
    assert (gateway["error_ratio"], gateway["mean_latency_ms"]) == (0.01, 50.0)
    assert gateway["probe"]["status_code"] == 200 and gateway["checked_at"]
    assert snapshot["metrics"] == "available"


HEALTHY = {"api-gateway": (10, 0, 0.5, 10), "orders-service": (10, 0, 0.5, 10)}


async def test_a_failing_probe_means_unavailable_whatever_the_metrics_say():
    for failure in (httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), 503):
        snapshot = await monitor(probes={"orders": failure}, metrics=prometheus(HEALTHY)).check()
        assert states(snapshot) == {"api-gateway": "healthy", "orders-service": "unavailable"}, failure
        orders = next(row for row in snapshot["services"] if row["service"] == "orders-service")
        assert orders["probe"]["ok"] is False and orders["reasons"] == ["probe_failed"]


async def test_without_prometheus_a_ready_service_is_unknown_not_healthy():
    for metrics in (None, 500):  # unreachable, or answering with an error
        snapshot = await monitor(probes={"orders": 503}, metrics=metrics).check()
        assert snapshot["metrics"] == "unavailable"
        assert states(snapshot) == {"api-gateway": "unknown", "orders-service": "unavailable"}
        gateway = next(row for row in snapshot["services"] if row["service"] == "api-gateway")
        assert gateway["reasons"] == ["metrics_unavailable"] and gateway["error_ratio"] is None


async def test_a_service_prometheus_has_never_seen_is_unknown():
    snapshot = await monitor(metrics=prometheus({"api-gateway": (10, 0, 0.5, 10)})).check()
    assert states(snapshot) == {"api-gateway": "healthy", "orders-service": "unknown"}
    orders = next(row for row in snapshot["services"] if row["service"] == "orders-service")
    assert orders["reasons"] == ["no_metrics"]


async def test_an_idle_service_with_no_recent_requests_is_healthy():
    snapshot = await monitor(
        metrics=prometheus({"api-gateway": (0, 0, 0, 0), "orders-service": (0, 0, 0, 0)})
    ).check()
    assert states(snapshot) == {"api-gateway": "healthy", "orders-service": "healthy"}
