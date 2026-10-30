"""Duration histograms are recorded in seconds, so their buckets must be second-scale.

With the SDK's default bounds (0, 5, 10 ... 10000, meant for milliseconds) every request fell in
the first bucket and Prometheus' histogram_quantile returned ~4.75 s for any traffic.
"""

from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from services.shared.config import Settings
from services.shared.telemetry import DURATION_BUCKETS_SECONDS, Telemetry

DURATIONS = ("http.server.duration", "dependency.duration", "db.query.duration", "db.pool.acquire.duration")


def recorded(samples, name="http.server.duration"):
    reader = InMemoryMetricReader()
    telemetry = Telemetry("orders-service", Settings(otlp_endpoint=""), metric_reader=reader)
    for seconds in samples:
        telemetry.duration(name, seconds, {"http.route": "/x"})
    points = [
        point
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]
    telemetry.close()
    return points[0]


def histogram_quantile(q, bounds, counts):
    """Prometheus' histogram_quantile: linear interpolation inside the bucket holding rank q*total."""
    rank, cumulative, lower = q * sum(counts), 0, 0.0
    for upper, count in zip([*bounds, float("inf")], counts, strict=True):
        if cumulative + count >= rank:
            if upper == float("inf"):
                return lower
            return lower + (upper - lower) * (rank - cumulative) / count
        cumulative, lower = cumulative + count, upper
    return lower


def test_every_duration_histogram_uses_second_scale_buckets():
    for name in DURATIONS:
        point = recorded([0.05, 0.25, 2.0], name)
        assert tuple(point.explicit_bounds) == DURATION_BUCKETS_SECONDS, name
        assert sum(1 for count in point.bucket_counts if count) == 3, name  # three distinct buckets


def test_p95_from_the_buckets_is_close_to_the_real_latency():
    samples = [0.04] * 90 + [0.8] * 10  # p95 of the real data is 0.8 s
    point = recorded(samples)
    p95 = histogram_quantile(0.95, point.explicit_bounds, point.bucket_counts)
    assert 0.5 <= p95 <= 1.0, p95
    assert histogram_quantile(0.5, point.explicit_bounds, point.bucket_counts) <= 0.05


def test_timeouts_up_to_the_gateway_dependency_limit_stay_measurable():
    point = recorded([11.0])  # the gateway waits up to 12 s for orders
    assert DURATION_BUCKETS_SECONDS[-1] >= 12
    assert point.bucket_counts[-1] == 0  # not lost in the +Inf bucket
