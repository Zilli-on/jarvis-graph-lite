"""Change detector smoke tests."""

from __future__ import annotations

import unittest

from conftest import cleanup, prepare_sample_repo

from jarvis_graph.change_detector import detect_changes


class DetectChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_sample_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_no_changes_after_fresh_index(self) -> None:
        rep = detect_changes(self.repo)
        self.assertEqual(rep.added, [])
        self.assertEqual(rep.modified, [])
        self.assertEqual(rep.removed, [])
        self.assertEqual(rep.recommendation, "no_changes")
        self.assertEqual(rep.unchanged_count, 7)

    def test_detects_added_file(self) -> None:
        new_file = self.repo / "extra.py"
        new_file.write_text("def new_func() -> int:\n    return 42\n", encoding="utf-8")
        rep = detect_changes(self.repo)
        self.assertIn("extra.py", rep.added)
        self.assertEqual(rep.recommendation, "incremental")

    def test_detects_modified_file(self) -> None:
        target = self.repo / "helpers.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n# extra comment\n",
            encoding="utf-8",
        )
        rep = detect_changes(self.repo)
        self.assertIn("helpers.py", rep.modified)

    def test_detects_removed_file(self) -> None:
        (self.repo / "package" / "worker.py").unlink()
        rep = detect_changes(self.repo)
        self.assertIn("package/worker.py", rep.removed)


if __name__ == "__main__":
    unittest.main()
