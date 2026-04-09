"""find_dead_code engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.dead_code_engine import find_dead_code


class DeadCodeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()
        self.report = find_dead_code(self.repo)

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_total_checked_nonzero(self) -> None:
        self.assertGreater(self.report.total_checked, 0)

    def test_really_dead_is_flagged(self) -> None:
        flagged = {(d.name, d.rel_path) for d in self.report.dead}
        self.assertIn(("really_dead", "dead_function.py"), flagged)

    def test_used_function_not_flagged(self) -> None:
        # `_PRELOADED = used_function()` at module-level creates a textual
        # caller in dead_function.py, so used_function is alive.
        for d in self.report.dead:
            if d.rel_path == "dead_function.py":
                self.assertNotEqual(d.name, "used_function")

    def test_lazy_caller_function_flagged(self) -> None:
        # `call_lazily` exists in the fixture but is never called.
        flagged = {(d.name, d.rel_path) for d in self.report.dead}
        self.assertIn(("call_lazily", "lazy_caller.py"), flagged)

    def test_dunder_excluded(self) -> None:
        # __init__ in GreetingService must not appear
        names = {d.name for d in self.report.dead}
        self.assertNotIn("__init__", names)
        self.assertGreater(self.report.excluded_dunder, 0)

    def test_main_excluded_as_entrypoint(self) -> None:
        names = {d.name for d in self.report.dead}
        self.assertNotIn("main", names)


if __name__ == "__main__":
    unittest.main()
