import unittest
from metrics_agent.profiler import classify_rule, profile_metrics


class TestMetricsProfiler(unittest.TestCase):
    def test_classify_rule(self):
        self.assertEqual(classify_rule("pool_pressure"), "resource_saturation")
        self.assertEqual(classify_rule("pool_timeouts"), "resource_saturation")
        self.assertEqual(classify_rule("cpu_usage"), "resource_saturation")
        self.assertEqual(classify_rule("http_latency"), "latency")
        self.assertEqual(classify_rule("http_errors"), "error_rate")
        self.assertEqual(classify_rule("dependency_errors"), "error_rate")
        self.assertEqual(classify_rule("payment_failures"), "business_decline")
        self.assertEqual(classify_rule("custom_stat"), "other")

    def test_profile_pool_saturation(self):
        obs = [
            {
                "service": "orders-service",
                "rule": "pool_pressure",
                "phase": "trigger",
                "value": 0.95,
                "threshold": 0.9,
                "unit": "ratio",
                "evidence": {"window_end": "2026-11-04T10:00:00Z"},
            },
            {
                "service": "orders-service",
                "rule": "pool_pressure",
                "phase": "latest",
                "value": 0.95,
                "threshold": 0.9,
                "unit": "ratio",
                "evidence": {"window_end": "2026-11-04T10:00:50Z"},
            },
            {
                "service": "api-gateway",
                "rule": "http_errors",
                "phase": "trigger",
                "value": 0.5,
                "threshold": 0.15,
                "unit": "ratio",
                "evidence": {"window_end": "2026-11-04T10:00:10Z"},
            },
        ]
        incident = {"affected_services": ["orders-service", "api-gateway", "payments-service"]}
        prof = profile_metrics(obs, incident)

        self.assertEqual(prof["sample"]["observations_count"], 3)
        self.assertEqual(prof["sample"]["metrics_count"], 2)
        self.assertEqual(len(prof["resource_saturations"]), 1)
        self.assertEqual(prof["resource_saturations"][0]["service"], "orders-service")
        self.assertEqual(prof["resource_saturations"][0]["rule"], "pool_pressure")
        self.assertEqual(prof["resource_saturations"][0]["severity"], "critical")
        self.assertIn("payments-service", prof["services_missing"])
        self.assertEqual(prof["services_seen"], ["api-gateway", "orders-service"])

    def test_trend_recovered(self):
        obs = [
            {
                "service": "payments-service",
                "rule": "payment_failures",
                "phase": "trigger",
                "value": 0.3,
                "threshold": 0.25,
                "unit": "ratio",
            },
            {
                "service": "payments-service",
                "rule": "payment_failures",
                "phase": "latest",
                "value": 0.05,
                "threshold": 0.25,
                "unit": "ratio",
            },
        ]
        incident = {"affected_services": ["payments-service"]}
        prof = profile_metrics(obs, incident)
        m = prof["metrics"][0]
        self.assertEqual(m["trend"], "recovered")


if __name__ == "__main__":
    unittest.main()
