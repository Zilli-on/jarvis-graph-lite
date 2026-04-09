"""find_god_files engine smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.god_files_engine import find_god_files


class GodFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_total_files_matches_repo(self) -> None:
        rep = find_god_files(self.repo, limit=20)
        self.assertGreater(rep.total_files, 0)
        # We have at least the canned + extension fixture files.
        self.assertGreaterEqual(rep.total_files, 8)

    def test_returns_files_with_score(self) -> None:
        rep = find_god_files(self.repo, limit=20)
        self.assertGreater(len(rep.files), 0)
        for f in rep.files:
            self.assertGreaterEqual(f.score, 0.0)
            self.assertLessEqual(f.score, 1.0)
            self.assertGreater(f.symbol_count, 0)

    def test_helpers_appears_in_top(self) -> None:
        # helpers.py is imported by 3+ files in the extended fixture and
        # has multiple symbols → it should appear in the god-files list.
        rep = find_god_files(self.repo, limit=20)
        paths = {f.rel_path for f in rep.files}
        self.assertTrue(
            any("helpers" in p for p in paths),
            f"helpers.py missing from god-files: {paths}",
        )

    def test_score_is_sorted_descending(self) -> None:
        rep = find_god_files(self.repo, limit=20)
        scores = [f.score for f in rep.files]
        self.assertEqual(scores, sorted(scores, reverse=True))


if __name__ == "__main__":
    unittest.main()
