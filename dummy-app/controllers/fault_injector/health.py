"""Operator-facing service health: a real readiness probe plus Prometheus request metrics.

Health is never derived from fault state. Each check probes every service's ``/health`` and reads
its recent 5xx share and mean latency from Prometheus.
"""

import asyncio
import os
import time
from typing import Literal

import httpx
from pydantic import BaseModel

from services.shared.telemetry import utcnow

WINDOW = "1m"
DEGRADED_ERROR_RATIO = 0.05  # share of requests answered with 5xx
DEGRADED_MEAN_LATENCY_SECONDS = 1.0
# Mean latency, not a percentile: the duration histogram's bucket bounds do not fit its units.
QUERIES = {
    "requests": f"sum by (service_name) (rate(http_server_requests_total[{WINDOW}]))",
    "errors": f'sum by (service_name) (rate(http_server_requests_total{{http_status_code=~"5.."}}[{WINDOW}]))',
    "seconds": f"sum by (service_name) (rate(http_server_duration_seconds_sum[{WINDOW}]))",
    "observations": f"sum by (service_name) (rate(http_server_duration_seconds_count[{WINDOW}]))",
}


class Probe(BaseModel):
    ok: bool
    status_code: int | None
    latency_ms: float


class ServiceHealth(BaseModel):
    service: str
    state: Literal["healthy", "degraded", "unavailable", "unknown"]
    checked_at: str
    probe: Probe
    error_ratio: float | None  # share of requests answered with 5xx over the last minute
    mean_latency_ms: float | None
    reasons: list[str]


class HealthSnapshot(BaseModel):
    checked_at: str | None  # None until the first check has run
    metrics: Literal["available", "unavailable", "unknown"]
    services: list[ServiceHealth]


# Probed by the controller; each must also report request metrics to Prometheus.
HEALTH_SERVICES = {
    "api-gateway": "http://gateway:8000",
    "orders-service": "http://orders:8001",
    "inventory-service": "http://inventory:8002",
    "payments-service": "http://payments:8003",
}


def default_health():
    """Real probes plus Prometheus when PROMETHEUS_URL is configured (the Compose controller)."""
    url = os.getenv("PROMETHEUS_URL")
    if not url:
        return None
    client = httpx.AsyncClient(timeout=2, limits=httpx.Limits(max_connections=8))
    return HealthMonitor(client, HEALTH_SERVICES, url)


class HealthMonitor:
    def __init__(self, client, services, prometheus_url):
        """``services`` maps a service name to its base URL; ``client`` is an httpx.AsyncClient."""
        self.client, self.services, self.prometheus_url = client, services, prometheus_url

    async def check(self):
        """Probe every service and read Prometheus once; returns the operator-facing snapshot.

        States: unavailable (probe failed), unknown (ready, but no metrics to judge it by),
        degraded (5xx share or mean latency above threshold) or healthy.
        """
        probes = await asyncio.gather(*(self._probe(url) for url in self.services.values()))
        metrics = await self._metrics()
        rows = [
            self._classify(name, probe, metrics) for name, probe in zip(self.services, probes, strict=True)
        ]
        return {
            "checked_at": utcnow(),
            "metrics": "unavailable" if metrics is None else "available",
            "services": rows,
        }

    async def _probe(self, url):
        started = time.perf_counter()
        try:
            response = await self.client.get(url + "/health")
            status = response.status_code
        except httpx.HTTPError:
            status = None
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {"ok": status == 200, "status_code": status, "latency_ms": latency_ms}

    async def _metrics(self):
        """Rates by service and query name, or None when Prometheus cannot answer."""
        values = {}
        try:
            for name, query in QUERIES.items():
                response = await self.client.get(
                    self.prometheus_url + "/api/v1/query", params={"query": query}
                )
                response.raise_for_status()
                for row in response.json()["data"]["result"]:
                    values.setdefault(row["metric"]["service_name"], {})[name] = float(row["value"][1])
        except (httpx.HTTPError, ValueError, KeyError):
            return None
        return values

    @staticmethod
    def _classify(name, probe, metrics):
        row = {
            "service": name,
            "checked_at": utcnow(),
            "probe": probe,
            "error_ratio": None,
            "mean_latency_ms": None,
        }
        rates = (metrics or {}).get(name)
        if rates is not None:
            requests, observations = rates.get("requests", 0), rates.get("observations", 0)
            row["error_ratio"] = round(rates.get("errors", 0) / requests, 4) if requests else 0.0
            mean = rates.get("seconds", 0) / observations if observations else 0.0
            row["mean_latency_ms"] = round(mean * 1000, 1)
        if not probe["ok"]:
            return {**row, "state": "unavailable", "reasons": ["probe_failed"]}
        if rates is None:
            return {
                **row,
                "state": "unknown",
                "reasons": ["metrics_unavailable" if metrics is None else "no_metrics"],
            }
        reasons = []
        if row["error_ratio"] >= DEGRADED_ERROR_RATIO:
            reasons.append("high_error_ratio")
        if row["mean_latency_ms"] >= DEGRADED_MEAN_LATENCY_SECONDS * 1000:
            reasons.append("high_latency")
        return {**row, "state": "degraded" if reasons else "healthy", "reasons": reasons}
