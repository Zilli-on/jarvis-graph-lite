"""repo_summary smoke tests.

This was the only outstanding coverage gap on jarvis-graph-lite itself
when v0.9 (`find_coverage_gaps` integrated into `health_report`) was
shipped: `summarize` had cyclomatic 6, line count 70, zero tests, and
exactly one (CLI) caller. Closing the gap also doubles as end-to-end
validation that adding a test for a previously-unreached symbol makes
it disappear from the coverage-gap list.
"""

from __future__ import annotations

import json
import unittest

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.repo_summary import RepoSummary, summarize
from jarvis_graph.utils import repo_data_dir


class RepoSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()
        self.summary = summarize(self.repo)

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_returns_dataclass_with_repo_path(self) -> None:
        self.assertIsInstance(self.summary, RepoSummary)
        self.assertEqual(self.summary.repo_path, str(self.repo.resolve()))

    def test_counts_match_extended_fixture(self) -> None:
        # The extended fixture writes 5 extra files on top of the sample
        # repo (dead_function, unused_import, cycle_a, cycle_b,
        # complex_module). Exact totals are fragile across fixture edits;
        # we assert lower bounds + structural coherence instead.
        self.assertGreaterEqual(self.summary.files, 5)
        self.assertGreater(self.summary.symbols, 0)
        # Symbols are partitioned into kinds — the per-kind counts should
        # never exceed the total.
        kind_total = (
            self.summary.functions
            + self.summary.classes
            + self.summary.methods
            + self.summary.constants
        )
        self.assertLessEqual(kind_total, self.summary.symbols)
        self.assertGreaterEqual(self.summary.imports, 1)
        self.assertGreaterEqual(self.summary.calls, 1)

    def test_top_lists_are_descending(self) -> None:
        # most_imported_files and largest_files_by_symbols are sorted
        # by their second tuple element descending.
        for lst in (
            self.summary.most_imported_files,
            self.summary.largest_files_by_symbols,
        ):
            counts = [n for _, n in lst]
            self.assertEqual(counts, sorted(counts, reverse=True))

    def test_top_lists_are_capped_at_15(self) -> None:
        self.assertLessEqual(len(self.summary.most_imported_files), 15)
        self.assertLessEqual(len(self.summary.largest_files_by_symbols), 15)

    def test_likely_entrypoints_recognised(self) -> None:
        # The sample fixture's `app.py` doesn't match the entrypoint
        # patterns (no main.py, no cli.py, no __main__.py) so the list
        # may legitimately be empty. We just assert it's a list of str.
        self.assertIsInstance(self.summary.likely_entrypoints, list)
        for p in self.summary.likely_entrypoints:
            self.assertIsInstance(p, str)

    def test_writes_summary_json_to_data_dir(self) -> None:
        out_path = repo_data_dir(self.repo) / "summaries" / "repo_summary.json"
        self.assertTrue(
            out_path.exists(),
            f"summarize() must persist its output to {out_path}",
        )
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["repo_path"], self.summary.repo_path)
        self.assertEqual(loaded["files"], self.summary.files)
        self.assertEqual(loaded["symbols"], self.summary.symbols)


if __name__ == "__main__":
    unittest.main()
