import unittest
from metrics_agent.reader import read_all_metrics


class TestMetricsReader(unittest.TestCase):
    def test_single_page(self):
        page = {
            "items": [{"rule": "http_errors", "value": 0.5}],
            "total_records": 1,
            "has_more": False,
            "query_window": {"start": "2026-11-04T10:00:00Z"},
            "metadata": {"coverage": "complete"},
        }
        obs, gaps, qwin, meta = read_all_metrics(lambda c, l: page)
        self.assertEqual(len(obs), 1)
        self.assertEqual(len(gaps), 0)
        self.assertEqual(qwin["start"], "2026-11-04T10:00:00Z")
        self.assertEqual(meta["coverage"], "complete")

    def test_multi_page(self):
        pages = [
            {"items": [{"id": 1}], "has_more": True, "next_cursor": 1, "total_records": 2},
            {"items": [{"id": 2}], "has_more": False, "total_records": 2},
        ]
        def fetch(cursor, limit):
            return pages[cursor]

        obs, gaps, _, _ = read_all_metrics(fetch, page_size=1)
        self.assertEqual(len(obs), 2)
        self.assertEqual(len(gaps), 0)

    def test_error_handling(self):
        def fetch(cursor, limit):
            return {"error": "connection timeout"}

        obs, gaps, _, _ = read_all_metrics(fetch)
        self.assertEqual(len(obs), 0)
        self.assertTrue(any("connection timeout" in g for g in gaps))

    def test_cursor_stagnation(self):
        def fetch(cursor, limit):
            return {"items": [{"id": 1}], "has_more": True, "next_cursor": 0}

        obs, gaps, _, _ = read_all_metrics(fetch)
        self.assertEqual(len(obs), 1)
        self.assertTrue(any("did not advance" in g for g in gaps))


if __name__ == "__main__":
    unittest.main()
