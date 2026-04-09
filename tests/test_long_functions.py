"""find_long_functions engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.long_functions_engine import find_long_functions


class LongFunctionsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_total_callables_nonzero(self) -> None:
        rep = find_long_functions(self.repo, threshold=1, limit=100)
        self.assertGreater(rep.total_callables, 0)
        self.assertGreater(rep.average, 0)

    def test_long_but_simple_flagged_at_low_threshold(self) -> None:
        # `long_but_simple` has ~24 lines and complexity 1.
        rep = find_long_functions(self.repo, threshold=20, limit=100)
        names = {fn.name for fn in rep.functions}
        self.assertIn("long_but_simple", names)

    def test_simple_not_flagged_at_high_threshold(self) -> None:
        rep = find_long_functions(self.repo, threshold=200, limit=100)
        self.assertEqual(rep.functions, [])

    def test_dunder_excluded(self) -> None:
        rep = find_long_functions(self.repo, threshold=1, limit=200)
        names = {fn.name for fn in rep.functions}
        self.assertNotIn("__init__", names)


if __name__ == "__main__":
    unittest.main()
