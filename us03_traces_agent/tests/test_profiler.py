import unittest
from traces_agent.profiler import profile_traces


class TestTracesProfiler(unittest.TestCase):
    def test_call_tree_and_deepest_failure_detection(self):
        # api-gateway (root) -> orders-service (child) -> payments-service (leaf)
        spans = [
            {
                "span_id": "s1",
                "service": "api-gateway",
                "operation": "POST /api/orders",
                "error": True,
                "http_status_code": 500,
                "duration_us": 30000,
                "references": [],
            },
            {
                "span_id": "s2",
                "service": "orders-service",
                "operation": "POST /orders",
                "error": True,
                "http_status_code": 500,
                "duration_us": 25000,
                "references": [{"refType": "CHILD_OF", "spanID": "s1"}],
            },
            {
                "span_id": "s3",
                "service": "payments-service",
                "operation": "POST /payments/charge",
                "error": True,
                "http_status_code": 500,
                "duration_us": 10000,
                "references": [{"refType": "CHILD_OF", "spanID": "s2"}],
            },
        ]
        traces = [{"trace_id": "t1", "ref_id": "r1", "spans": spans}]
        incident = {"affected_services": ["api-gateway", "orders-service", "payments-service"]}

        prof = profile_traces(traces, incident)
        self.assertEqual(prof["sample"]["traces_count"], 1)
        self.assertEqual(prof["sample"]["failed_spans_count"], 3)
        self.assertEqual(prof["deepest_failure_origin"], "payments-service")
        self.assertIn("api-gateway -> orders-service -> payments-service", prof["propagation_paths"])
        self.assertEqual(prof["services_seen"], ["api-gateway", "orders-service", "payments-service"])
        self.assertEqual(prof["services_missing"], [])

        # Check pattern flags
        p_payments = next(p for p in prof["patterns"] if p["service"] == "payments-service")
        self.assertTrue(p_payments["is_deepest_origin"])


if __name__ == "__main__":
    unittest.main()
