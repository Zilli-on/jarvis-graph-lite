"""find_circular_deps engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo, prepare_sample_repo

from jarvis_graph.circular_deps_engine import find_circular_deps


class CircularDepsTests(unittest.TestCase):
    def test_no_cycles_in_clean_sample(self) -> None:
        tmp, repo = prepare_sample_repo()
        try:
            rep = find_circular_deps(repo)
            self.assertEqual(rep.cycles, [])
            self.assertGreater(rep.total_files, 0)
        finally:
            cleanup(tmp)

    def test_extended_repo_finds_cycle_a_b(self) -> None:
        tmp, repo = prepare_extended_repo()
        try:
            rep = find_circular_deps(repo)
            self.assertGreaterEqual(len(rep.cycles), 1)
            cycle_files = {f for c in rep.cycles for f in c.files}
            self.assertIn("cycle_a.py", cycle_files)
            self.assertIn("cycle_b.py", cycle_files)
            ab_cycle = next(
                (c for c in rep.cycles if "cycle_a.py" in c.files and "cycle_b.py" in c.files),
                None,
            )
            self.assertIsNotNone(ab_cycle)
            self.assertEqual(ab_cycle.size, 2)
        finally:
            cleanup(tmp)


if __name__ == "__main__":
    unittest.main()
