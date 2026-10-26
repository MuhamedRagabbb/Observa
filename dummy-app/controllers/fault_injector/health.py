"""Operator-facing service health: readiness probes where the controller already has network
access, plus Prometheus request metrics for every service.

Health is never derived from fault state, and missing evidence is never reported as healthy:
- unavailable: the service's /health probe failed;
- unknown: no trustworthy metrics (Prometheus down, Collector scrape down, never seen, or, for a
  service that is not probed, no requests in the window; a stopped service's series linger in the
  Collector for minutes, so only fresh requests prove it is serving);
- degraded: 5xx share or mean latency above threshold;
- healthy: otherwise.
"""

import asyncio
import os
import time
from typing import Literal

import httpx
from pydantic import BaseModel

from services.shared.telemetry import utcnow

WINDOW = "30s"  # Prometheus scrapes every 3 s and services export every 3 s
DEGRADED_ERROR_RATIO = 0.05  # share of requests answered with 5xx
DEGRADED_MEAN_LATENCY_SECONDS = 1.0
# Mean latency, not a percentile: the duration histogram's bucket bounds are milliseconds while
# its values are seconds, so its quantiles are meaningless (tracked for P2-C).
QUERIES = {
    "requests": f"sum by (service_name) (rate(http_server_requests_total[{WINDOW}]))",
    "errors": f'sum by (service_name) (rate(http_server_requests_total{{http_status_code=~"5.."}}[{WINDOW}]))',
    "seconds": f"sum by (service_name) (rate(http_server_duration_seconds_sum[{WINDOW}]))",
    "observations": f"sum by (service_name) (rate(http_server_duration_seconds_count[{WINDOW}]))",
}
SCRAPE_UP = 'up{job="application-otel"}'  # Prometheus scraping the Collector's metrics endpoint


class Probe(BaseModel):
    ok: bool
    status_code: int | None
    latency_ms: float


class ServiceHealth(BaseModel):
    service: str
    state: Literal["healthy", "degraded", "unavailable", "unknown"]
    checked_at: str
    probe: Probe | None  # None: not reachable from the controller, judged from metrics
    error_ratio: float | None  # share of 5xx answers in the window
    mean_latency_ms: float | None
    reasons: list[str]


class HealthSnapshot(BaseModel):
    checked_at: str | None  # None until the first check has run
    metrics: Literal["available", "unavailable", "unknown"]
    services: list[ServiceHealth]


# Services the controller reports on. Only Orders and Payments share the controller's control
# network, so only they are probed; the rest are judged from Prometheus alone.
HEALTH_SERVICES = {
    "api-gateway": None,
    "orders-service": "http://orders:8001",
    "inventory-service": None,
    "payments-service": "http://payments:8003",
}


def default_health():
    """Probes plus Prometheus when PROMETHEUS_URL is configured (the Compose controller)."""
    url = os.getenv("PROMETHEUS_URL")
    if not url:
        return None
    client = httpx.AsyncClient(timeout=2, limits=httpx.Limits(max_connections=8))
    return HealthMonitor(client, HEALTH_SERVICES, url)


class HealthMonitor:
    def __init__(self, client, services, prometheus_url):
        """``services`` maps a service name to its probe base URL, or None when the controller
        cannot reach it and must judge it from Prometheus alone."""
        self.client, self.services, self.prometheus_url = client, services, prometheus_url

    async def check(self):
        """Probe the reachable services and read Prometheus once; returns the operator snapshot."""
        probes = await asyncio.gather(*(self._probe(url) for url in self.services.values()))
        metrics, trouble = await self._metrics()
        rows = [
            self._classify(name, probe, metrics, trouble)
            for name, probe in zip(self.services, probes, strict=True)
        ]
        return {
            "checked_at": utcnow(),
            "metrics": "unavailable" if trouble else "available",
            "services": rows,
        }

    async def _probe(self, url):
        if url is None:
            return None
        started = time.perf_counter()
        try:
            response = await self.client.get(url + "/health")
            status = response.status_code
        except httpx.HTTPError:
            status = None
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return {"ok": status == 200, "status_code": status, "latency_ms": latency_ms}

    async def _query(self, query):
        response = await self.client.get(self.prometheus_url + "/api/v1/query", params={"query": query})
        response.raise_for_status()
        return response.json()["data"]["result"]

    async def _metrics(self):
        """(rates by service and query name, None) or (None, why the metrics cannot be trusted)."""
        try:
            scrape = await self._query(SCRAPE_UP)
            if not any(float(row["value"][1]) == 1 for row in scrape):
                return None, "scrape_down"
            values = {}
            for name, query in QUERIES.items():
                for row in await self._query(query):
                    values.setdefault(row["metric"]["service_name"], {})[name] = float(row["value"][1])
        except (httpx.HTTPError, ValueError, KeyError):
            return None, "metrics_unavailable"
        return values, None

    @staticmethod
    def _classify(name, probe, metrics, trouble):
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
        if probe is not None and not probe["ok"]:
            return {**row, "state": "unavailable", "reasons": ["probe_failed"]}
        if trouble:
            return {**row, "state": "unknown", "reasons": [trouble]}
        if rates is None:
            return {**row, "state": "unknown", "reasons": ["no_metrics"]}
        if probe is None and not rates.get("requests"):
            return {**row, "state": "unknown", "reasons": ["stale_metrics"]}
        reasons = []
        if row["error_ratio"] >= DEGRADED_ERROR_RATIO:
            reasons.append("high_error_ratio")
        if row["mean_latency_ms"] >= DEGRADED_MEAN_LATENCY_SECONDS * 1000:
            reasons.append("high_latency")
        return {**row, "state": "degraded" if reasons else "healthy", "reasons": reasons}
