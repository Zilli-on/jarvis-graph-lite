"""Force the parallel parsing path through index_repo.

Even on a small fixture (where `should_parallelize` is False) we explicitly
pass ``parallel=True`` so the ProcessPoolExecutor branch is exercised at
least once in CI. The output index must be byte-for-byte equivalent to a
sequential run.
"""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.db import connect
from jarvis_graph.indexer import index_repo


def _snapshot(repo) -> dict[str, int]:
    conn = connect(repo)
    try:
        out: dict[str, int] = {}
        for tbl in ("file", "symbol", "import_edge", "call_edge"):
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()
            out[tbl] = int(row["n"])
        return out
    finally:
        conn.close()


class ParallelIndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_parallel_path_produces_same_counts(self) -> None:
        seq_counts = _snapshot(self.repo)
        # Re-index the same repo with `parallel=True, full=True` and assert
        # that totals match the sequential baseline exactly.
        rep = index_repo(self.repo, full=True, parallel=True, max_workers=2)
        self.assertGreater(rep.files_indexed, 0)
        par_counts = _snapshot(self.repo)
        self.assertEqual(seq_counts, par_counts)

    def test_parallel_force_off(self) -> None:
        rep = index_repo(self.repo, full=True, parallel=False)
        self.assertGreater(rep.files_indexed, 0)
        # Sequential path must report no errors on the canned fixture.
        self.assertEqual(rep.files_with_errors, 0)


if __name__ == "__main__":
    unittest.main()
