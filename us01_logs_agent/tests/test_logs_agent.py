import unittest

from logs_agent.profiler import classify, profile_logs
from logs_agent.reader import read_all_logs

INCIDENT = {
    "affected_services": ["payments-service", "orders-service", "api-gateway"],
    "first_observed_at": "2026-11-16T22:52:28.810759Z",
}


def rec(ts, service, event, level="ERROR", **kw):
    base = {"timestamp": ts, "service.name": service, "event": event,
            "level": level, "environment": "local"}
    base.update(kw)
    return base


def sample():
    """Shape copied from the real live incident (orders + gateway only)."""
    r = []
    for i in range(3):
        t = f"2026-11-16T22:55:{10 + i}.000Z"
        r.append(rec(t, "orders-service", "order_failed", **{"error.type": "payment_unavailable"},
                     trace_id=f"t{i}"))
        r.append(rec(t, "api-gateway", "request_completed", **{"http.status_code": 503},
                     trace_id=f"t{i}", duration_ms=1200 + i))
    r.append(rec("2026-11-16T22:55:20.000Z", "orders-service", "order_failed",
                 **{"error.type": "product_not_found"}))
    r.append(rec("2026-11-16T22:55:21.000Z", "api-gateway", "request_completed", "WARN",
                 **{"http.status_code": 422}))
    r.append(rec("2026-11-16T22:55:22.000Z", "api-gateway", "validation_failed", "WARN"))
    return r


class ClassifyTests(unittest.TestCase):
    def test_service_failure(self):
        self.assertEqual(classify(rec("t", "x", "order_failed", **{"error.type": "payment_unavailable"})),
                         "service_failure")
        self.assertEqual(classify(rec("t", "x", "request_completed", **{"http.status_code": 503})),
                         "service_failure")

    def test_noise(self):
        self.assertEqual(classify(rec("t", "x", "order_failed", **{"error.type": "product_not_found"})),
                         "client_noise")
        self.assertEqual(classify(rec("t", "x", "request_completed", "WARN", **{"http.status_code": 404})),
                         "client_noise")
        self.assertEqual(classify(rec("t", "x", "validation_failed", "WARN")), "client_noise")

    def test_business_decline(self):
        self.assertEqual(classify(rec("t", "x", "request_completed", "WARN", **{"http.status_code": 402})),
                         "business_decline")


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.p = profile_logs(sample(), INCIDENT)

    def test_counts(self):
        self.assertEqual(self.p["categories"]["service_failure"], 6)
        self.assertEqual(self.p["categories"]["client_noise"], 3)
        self.assertEqual(self.p["sample"]["records"], 9)

    def test_patterns_exclude_noise_and_sort_by_count(self):
        self.assertTrue(all(x["category"] != "client_noise" for x in self.p["patterns"]))
        self.assertEqual(self.p["patterns"][0]["count"], 3)

    def test_p95_duration_for_gateway_pattern(self):
        gw = [x for x in self.p["patterns"] if x["service"] == "api-gateway"][0]
        self.assertEqual(gw["p95_duration_ms"], 1202)

    def test_sample_does_not_cover_incident_start(self):
        self.assertIs(self.p["sample"]["covers_incident_start"], False)
        self.assertTrue(any("first-in-sample" in x for x in self.p["limits"]))

    def test_missing_service_flagged(self):
        self.assertEqual(self.p["services_missing"], ["payments-service"])
        self.assertTrue(any("payments-service" in x for x in self.p["limits"]))

    def test_cross_service_traces(self):
        self.assertEqual(self.p["cross_service_traces"], 3)

    def test_release_change_detected(self):
        recs = sample() + [
            rec("2026-11-16T22:55:30.000Z", "orders-service", "request_completed", "INFO",
                **{"service.version": "v1", "deployment.id": "d1"}),
            rec("2026-11-16T22:55:31.000Z", "orders-service", "request_completed", "INFO",
                **{"service.version": "v2-buggy", "deployment.id": "d2"}),
        ]
        p = profile_logs(recs, INCIDENT)
        self.assertIn("orders-service", p["release_changes"])

    def test_rate_and_window(self):
        top = self.p["patterns"][0]
        self.assertEqual(top["window_seconds"], 2.0)
        self.assertEqual(top["rate_per_s"], 1.5)

    def test_other_events_listed(self):
        recs = sample() + [
            rec("2026-11-16T22:55:30.000Z", "orders-service", "release_changed", "INFO"),
        ]
        p = profile_logs(recs, INCIDENT)
        self.assertEqual(p["other_events"][0]["event"], "release_changed")
        self.assertEqual(p["other_events"][0]["count"], 1)

    def test_read_gaps_reach_limits(self):
        p = profile_logs([], INCIDENT, read_gaps=["logs read stopped at cursor 0: boom"])
        self.assertIn("log evidence could not be read", p["limits"])
        self.assertIn("logs read stopped at cursor 0: boom", p["limits"])
        self.assertEqual(p["read_gaps"], ["logs read stopped at cursor 0: boom"])

    def test_empty_records(self):
        p = profile_logs([], INCIDENT)
        self.assertEqual(p["sample"]["records"], 0)
        self.assertIn("no log evidence in this snapshot", p["limits"])


class ReaderTests(unittest.TestCase):
    def test_reads_all_pages(self):
        pages = {
            0: {"items": [1, 2], "next_cursor": 2, "has_more": True, "total_records": 3},
            2: {"items": [3], "next_cursor": 3, "has_more": False, "total_records": 3},
        }
        records, gaps = read_all_logs(lambda c, n: pages[c], page_size=2)
        self.assertEqual(records, [1, 2, 3])
        self.assertEqual(gaps, [])

    def test_error_page_becomes_gap(self):
        pages = {
            0: {"items": [1], "next_cursor": 1, "has_more": True, "total_records": 5},
            1: {"error": "record_too_large"},
        }
        records, gaps = read_all_logs(lambda c, n: pages[c])
        self.assertEqual(records, [1])
        self.assertTrue(any("record_too_large" in g for g in gaps))
        self.assertTrue(any("1 of 5" in g for g in gaps))

    def test_cursor_not_advancing_stops(self):
        page = {"items": [1], "next_cursor": 0, "has_more": True}
        _, gaps = read_all_logs(lambda c, n: page)
        self.assertTrue(any("did not advance" in g for g in gaps))


if __name__ == "__main__":
    unittest.main()
