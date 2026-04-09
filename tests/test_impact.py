"""Impact engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_sample_repo

from jarvis_graph.impact_engine import impact


class ImpactTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_sample_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_impact_for_widely_used_helper(self) -> None:
        res = impact(self.repo, "format_greeting")
        self.assertEqual(res.kind, "symbol")
        self.assertGreaterEqual(len(res.direct_importers), 2)
        self.assertIn(res.risk, {"low", "medium", "high"})
        self.assertTrue(res.why)

    def test_impact_for_file(self) -> None:
        res = impact(self.repo, "helpers.py")
        self.assertEqual(res.kind, "file")
        self.assertGreaterEqual(len(res.direct_importers), 2)

    def test_impact_for_unused_symbol(self) -> None:
        res = impact(self.repo, "shout")
        self.assertEqual(res.kind, "symbol")
        self.assertEqual(res.risk, "low")

    def test_impact_not_found(self) -> None:
        res = impact(self.repo, "nope_nope_nope_xyz")
        self.assertEqual(res.kind, "not_found")


if __name__ == "__main__":
    unittest.main()
