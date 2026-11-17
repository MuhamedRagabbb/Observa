import unittest
from traces_agent.reader import read_all_traces


class TestTracesReader(unittest.TestCase):
    def test_single_trace_summary_and_spans(self):
        def fetch_summary(c, l):
            return {
                "items": [{"trace_id": "t1", "ref_id": "ref-t1", "span_count": 1}],
                "total_records": 1,
                "has_more": False,
                "query_window": {"start": "2026-11-04T10:00:00Z"},
            }
        def fetch_spans(ref_id):
            return {"items": [{"span_id": "s1", "service": "api-gateway"}]}

        traces, gaps, qwin, _ = read_all_traces(fetch_summary, fetch_spans)
        self.assertEqual(len(traces), 1)
        self.assertEqual(len(gaps), 0)
        self.assertEqual(len(traces[0]["spans"]), 1)
        self.assertEqual(traces[0]["spans"][0]["service"], "api-gateway")

    def test_empty_traces(self):
        def fetch_summary(c, l):
            return {"items": [], "total_records": 0, "has_more": False}

        traces, gaps, _, _ = read_all_traces(fetch_summary)
        self.assertEqual(len(traces), 0)
        self.assertEqual(len(gaps), 0)

    def test_summary_error(self):
        def fetch_summary(c, l):
            return {"error": "traces database unreachable"}

        traces, gaps, _, _ = read_all_traces(fetch_summary)
        self.assertTrue(any("unreachable" in g for g in gaps))


if __name__ == "__main__":
    unittest.main()
