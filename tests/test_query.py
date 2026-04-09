"""Query engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_sample_repo

from jarvis_graph.query_engine import query


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_sample_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_query_finds_symbol_by_name(self) -> None:
        hits = query(self.repo, "format_greeting")
        self.assertTrue(hits, "expected at least one hit for 'format_greeting'")
        self.assertTrue(any(h.name == "format_greeting" for h in hits))
        self.assertEqual(hits[0].name, "format_greeting")

    def test_query_finds_class(self) -> None:
        hits = query(self.repo, "GreetingService")
        self.assertTrue(any(h.name == "GreetingService" and h.kind == "class" for h in hits))

    def test_query_drops_stopwords(self) -> None:
        hits = query(self.repo, "show me the worker")
        self.assertTrue(any("worker" in (h.rel_path or "").lower() for h in hits))

    def test_query_returns_empty_for_unknown(self) -> None:
        hits = query(self.repo, "definitelynotinthisrepo_xyz")
        self.assertEqual(hits, [])

    def test_query_respects_limit(self) -> None:
        hits = query(self.repo, "greeting", limit=2)
        self.assertLessEqual(len(hits), 2)


if __name__ == "__main__":
    unittest.main()
