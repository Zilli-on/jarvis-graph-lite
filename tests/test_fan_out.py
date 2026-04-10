"""Tests for find_high_fan_out (v0.6).

Builds a tiny synthetic repo with files of known import graphs so the
fan-out counts are deterministic regardless of the rest of the suite.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from conftest import cleanup  # noqa: F401 — also triggers sys.path setup

from jarvis_graph.fan_out_engine import find_high_fan_out
from jarvis_graph.indexer import index_repo


def _make_fanout_repo() -> tuple[Path, Path]:
    """Build a tiny fixture repo with known fan-out per file."""
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_fanout_"))
    repo = tmp_root / "fanout_repo"
    repo.mkdir()
    # 5 leaf modules with no imports
    for i in range(5):
        (repo / f"leaf_{i}.py").write_text(
            f"def leaf_{i}() -> int:\n    return {i}\n",
            encoding="utf-8",
        )
    # `hub.py` imports all 5 leaves → fan_out = 5
    (repo / "hub.py").write_text(
        "from leaf_0 import leaf_0\n"
        "from leaf_1 import leaf_1\n"
        "from leaf_2 import leaf_2\n"
        "from leaf_3 import leaf_3\n"
        "from leaf_4 import leaf_4\n"
        "\n"
        "def hub() -> int:\n"
        "    return leaf_0() + leaf_1() + leaf_2() + leaf_3() + leaf_4()\n",
        encoding="utf-8",
    )
    # `tiny.py` imports 2 leaves → fan_out = 2 (below default threshold 5)
    (repo / "tiny.py").write_text(
        "from leaf_0 import leaf_0\n"
        "from leaf_1 import leaf_1\n"
        "\n"
        "def tiny() -> int:\n"
        "    return leaf_0() + leaf_1()\n",
        encoding="utf-8",
    )
    # `mixed.py` does 3 in-repo imports + 2 stdlib imports.
    # fan_out should be 3 (only the in-repo ones count).
    (repo / "mixed.py").write_text(
        "import os\n"
        "import sys\n"
        "from leaf_2 import leaf_2\n"
        "from leaf_3 import leaf_3\n"
        "from leaf_4 import leaf_4\n"
        "\n"
        "def mixed() -> int:\n"
        "    return leaf_2() + leaf_3() + leaf_4() + len(os.name) + len(sys.platform)\n",
        encoding="utf-8",
    )
    # `dup.py` imports the same leaf twice (different bindings).
    # COUNT(DISTINCT resolved_file_id) should still be 1.
    (repo / "dup.py").write_text(
        "from leaf_0 import leaf_0 as a\n"
        "from leaf_0 import leaf_0 as b\n"
        "\n"
        "def dup() -> int:\n"
        "    return a() + b()\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


class FanOutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_fanout_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_hub_has_fan_out_five(self) -> None:
        rep = find_high_fan_out(self.repo, threshold=1)
        by_path = {f.rel_path: f for f in rep.files}
        self.assertIn("hub.py", by_path)
        self.assertEqual(by_path["hub.py"].fan_out, 5)

    def test_threshold_filters_tiny_file(self) -> None:
        # Default threshold = 5; tiny.py has fan_out=2, must be excluded.
        rep = find_high_fan_out(self.repo)  # threshold=5
        paths = [f.rel_path for f in rep.files]
        self.assertNotIn("tiny.py", paths)
        self.assertIn("hub.py", paths)

    def test_mixed_file_only_counts_inrepo_imports(self) -> None:
        rep = find_high_fan_out(self.repo, threshold=1)
        by_path = {f.rel_path: f for f in rep.files}
        # mixed.py has 5 import lines (2 stdlib + 3 in-repo) so:
        #   imports_total >= 5 (every import_edge row)
        #   fan_out == 3 (only the resolved in-repo ones)
        self.assertIn("mixed.py", by_path)
        self.assertEqual(by_path["mixed.py"].fan_out, 3)
        self.assertGreaterEqual(by_path["mixed.py"].imports_total, 5)
        self.assertEqual(by_path["mixed.py"].imports_resolved, 3)

    def test_duplicate_imports_collapse_to_one(self) -> None:
        # dup.py imports leaf_0 twice — distinct fan_out should be 1.
        rep = find_high_fan_out(self.repo, threshold=1)
        by_path = {f.rel_path: f for f in rep.files}
        self.assertIn("dup.py", by_path)
        self.assertEqual(by_path["dup.py"].fan_out, 1)
        # imports_total counts every edge: should be 2.
        self.assertEqual(by_path["dup.py"].imports_total, 2)

    def test_leaves_have_no_fan_out(self) -> None:
        rep = find_high_fan_out(self.repo, threshold=1)
        paths = [f.rel_path for f in rep.files]
        for i in range(5):
            self.assertNotIn(f"leaf_{i}.py", paths)

    def test_results_sorted_by_fan_out_desc(self) -> None:
        rep = find_high_fan_out(self.repo, threshold=1)
        fan_outs = [f.fan_out for f in rep.files]
        self.assertEqual(fan_outs, sorted(fan_outs, reverse=True))

    def test_limit_caps_results(self) -> None:
        rep = find_high_fan_out(self.repo, threshold=1, limit=2)
        self.assertLessEqual(len(rep.files), 2)

    def test_risk_buckets(self) -> None:
        rep = find_high_fan_out(self.repo, threshold=1)
        # In a 9-file repo, fan_out=5 is 5/9 ≈ 56% → high.
        hub = next(f for f in rep.files if f.rel_path == "hub.py")
        self.assertEqual(hub.risk, "high")

    def test_total_files_reported(self) -> None:
        rep = find_high_fan_out(self.repo, threshold=1)
        # 5 leaves + hub + tiny + mixed + dup = 9
        self.assertEqual(rep.total_files, 9)

    def test_empty_threshold_returns_no_files(self) -> None:
        # An impossibly high threshold filters everything.
        rep = find_high_fan_out(self.repo, threshold=999)
        self.assertEqual(rep.files, [])


if __name__ == "__main__":
    unittest.main()
