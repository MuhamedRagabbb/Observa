"""Service health for the operator (controllers/fault_injector/health.py): probes where the
controller already has network access, Prometheus for every service.

Never derived from fault state, and missing evidence is never reported as healthy. Each test
fakes the probes and Prometheus at the HTTP boundary.
"""

import httpx

from controllers.fault_injector.health import HealthMonitor

# The gateway is not reachable from the controller: it is judged from Prometheus alone.
SERVICES = {"api-gateway": None, "orders-service": "http://orders:8001"}
PROMETHEUS = "http://prometheus:9090"


def prometheus(rates, *, scrape_up=1):
    """Fake Prometheus.

    ``rates`` maps service -> (requests/s, 5xx/s, seconds/s, observations/s) over the window;
    ``scrape_up`` is the Collector scrape target's ``up`` value (None: no such target).
    """

    def answer(query):
        if query.startswith("up{"):
            return [] if scrape_up is None else [{"metric": {}, "value": [0, str(scrape_up)]}]
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


def transport(contacted, *, probes=None, metrics=None):
    probes = probes or {}

    def handle(request):
        contacted.append(request.url.host)
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


async def check(contacted=None, **kwargs):
    contacted = [] if contacted is None else contacted
    client = httpx.AsyncClient(transport=transport(contacted, **kwargs))
    return await HealthMonitor(client, SERVICES, PROMETHEUS).check()


def states(snapshot):
    return {row["service"]: row["state"] for row in snapshot["services"]}


def row(snapshot, service):
    return next(r for r in snapshot["services"] if r["service"] == service)


HEALTHY = {"api-gateway": (10, 0, 0.5, 10), "orders-service": (10, 0, 0.5, 10)}


async def test_fresh_metrics_with_few_5xx_and_normal_latency_are_healthy_otherwise_degraded():
    rates = {
        "api-gateway": (10, 0.1, 0.5, 10),  # 1% 5xx, mean 50 ms
        "orders-service": (10, 2, 0.5, 10),  # 20% 5xx
    }
    contacted = []
    snapshot = await check(contacted, metrics=prometheus(rates))

    assert states(snapshot) == {"api-gateway": "healthy", "orders-service": "degraded"}
    gateway = row(snapshot, "api-gateway")
    assert (gateway["error_ratio"], gateway["mean_latency_ms"]) == (0.01, 50.0)
    assert gateway["probe"] is None and "gateway" not in contacted  # never probed
    assert row(snapshot, "orders-service")["probe"]["status_code"] == 200
    assert snapshot["metrics"] == "available"


async def test_a_failing_probe_means_unavailable_whatever_the_metrics_say():
    for failure in (httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), 503):
        snapshot = await check(probes={"orders": failure}, metrics=prometheus(HEALTHY))
        assert states(snapshot) == {"api-gateway": "healthy", "orders-service": "unavailable"}, failure
        orders = row(snapshot, "orders-service")
        assert orders["probe"]["ok"] is False and orders["reasons"] == ["probe_failed"]


async def test_without_prometheus_nothing_is_healthy():
    for metrics in (None, 500):  # unreachable, or answering with an error
        snapshot = await check(metrics=metrics)
        assert snapshot["metrics"] == "unavailable"
        assert states(snapshot) == {"api-gateway": "unknown", "orders-service": "unknown"}
        assert row(snapshot, "api-gateway")["reasons"] == ["metrics_unavailable"]
        assert row(snapshot, "api-gateway")["error_ratio"] is None


async def test_when_prometheus_cannot_scrape_the_collector_its_numbers_are_not_trusted():
    for scrape_up in (0, None):
        snapshot = await check(metrics=prometheus(HEALTHY, scrape_up=scrape_up))
        assert snapshot["metrics"] == "unavailable"
        assert states(snapshot) == {"api-gateway": "unknown", "orders-service": "unknown"}
        assert row(snapshot, "orders-service")["reasons"] == ["scrape_down"]


async def test_a_service_prometheus_has_never_seen_is_unknown():
    snapshot = await check(metrics=prometheus({"orders-service": (10, 0, 0.5, 10)}))
    assert states(snapshot) == {"api-gateway": "unknown", "orders-service": "healthy"}
    assert row(snapshot, "api-gateway")["reasons"] == ["no_metrics"]


async def test_without_recent_requests_only_a_probed_service_can_be_healthy():
    # A stopped service's series linger in the Collector for minutes, so old numbers prove nothing.
    snapshot = await check(metrics=prometheus({"api-gateway": (0, 0, 0, 0), "orders-service": (0, 0, 0, 0)}))
    assert states(snapshot) == {"api-gateway": "unknown", "orders-service": "healthy"}
    assert row(snapshot, "api-gateway")["reasons"] == ["stale_metrics"]
