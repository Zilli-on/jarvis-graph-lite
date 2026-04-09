"""find_complexity engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.complexity_engine import find_complexity


class ComplexityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_total_callables_nonzero(self) -> None:
        rep = find_complexity(self.repo, threshold=1, limit=100)
        self.assertGreater(rep.total_callables, 0)
        # average must be sane (everyone is at least 1 = entry edge).
        self.assertGreaterEqual(rep.average, 1.0)

    def test_simple_function_has_complexity_1(self) -> None:
        rep = find_complexity(self.repo, threshold=1, limit=100)
        simples = [h for h in rep.hotspots if h.name == "simple"]
        self.assertTrue(simples, "simple() not found in hotspot list")
        self.assertEqual(simples[0].complexity, 1)
        self.assertEqual(simples[0].risk, "low")

    def test_tangled_function_has_high_complexity(self) -> None:
        rep = find_complexity(self.repo, threshold=1, limit=100)
        tangles = [h for h in rep.hotspots if h.name == "tangled"]
        self.assertTrue(tangles, "tangled() not found in hotspot list")
        # Hand-counted lower bound: at least 8 branches in this fixture.
        self.assertGreaterEqual(tangles[0].complexity, 8)

    def test_threshold_filter(self) -> None:
        rep = find_complexity(self.repo, threshold=999, limit=100)
        self.assertEqual(rep.hotspots, [])

    def test_dunder_methods_excluded_from_average(self) -> None:
        # The fixture's GreetingService has __init__; it must NOT be in
        # the hotspots even with threshold=1.
        rep = find_complexity(self.repo, threshold=1, limit=200)
        names = {h.name for h in rep.hotspots}
        self.assertNotIn("__init__", names)


if __name__ == "__main__":
    unittest.main()
