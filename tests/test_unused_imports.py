"""find_unused_imports engine smoke tests."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from conftest import cleanup, prepare_extended_repo

from jarvis_graph.indexer import index_repo
from jarvis_graph.unused_imports_engine import (
    _logical_import_line,
    _noqa_allows_unused_import,
    find_unused_imports,
)


class UnusedImportsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = prepare_extended_repo()
        self.report = find_unused_imports(self.repo)

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_total_imports_nonzero(self) -> None:
        self.assertGreater(self.report.total_imports, 0)

    def test_unused_os_flagged_in_unused_import_module(self) -> None:
        unused_in_file = [
            u for u in self.report.unused if u.rel_path == "unused_import.py"
        ]
        bindings = {u.binding for u in unused_in_file}
        self.assertIn("os", bindings)
        self.assertIn("load_config", bindings)

    def test_used_format_greeting_not_flagged(self) -> None:
        # `format_greeting` IS used in unused_import.py — must not appear
        for u in self.report.unused:
            if u.rel_path == "unused_import.py":
                self.assertNotEqual(u.binding, "format_greeting")

    def test_app_py_imports_clean(self) -> None:
        # All imports in app.py should be used.
        unused_in_app = [u for u in self.report.unused if u.rel_path == "app.py"]
        self.assertEqual(unused_in_app, [], f"unexpected unused in app.py: {unused_in_app}")


class NoqaDirectiveTests(unittest.TestCase):
    """Pure-helper tests for `_noqa_allows_unused_import`."""

    def test_blanket_noqa_suppresses(self) -> None:
        self.assertTrue(
            _noqa_allows_unused_import("import os  # noqa")
        )

    def test_specific_f401_suppresses(self) -> None:
        self.assertTrue(
            _noqa_allows_unused_import("import os  # noqa: F401")
        )

    def test_specific_f401_with_trailing_comment(self) -> None:
        self.assertTrue(
            _noqa_allows_unused_import(
                "from conftest import ROOT  # noqa: F401  path setup"
            )
        )

    def test_f401_in_mixed_codes_suppresses(self) -> None:
        self.assertTrue(
            _noqa_allows_unused_import("import os  # noqa: E501, F401")
        )

    def test_other_codes_do_not_suppress(self) -> None:
        self.assertFalse(
            _noqa_allows_unused_import("import os  # noqa: E501")
        )

    def test_no_noqa_at_all(self) -> None:
        self.assertFalse(
            _noqa_allows_unused_import("import os  # just a comment")
        )

    def test_case_insensitive(self) -> None:
        self.assertTrue(
            _noqa_allows_unused_import("import os  # NOQA: f401")
        )

    def test_no_space_variant(self) -> None:
        self.assertTrue(
            _noqa_allows_unused_import("import os  #noqa:F401")
        )


class LogicalImportLineTests(unittest.TestCase):
    """Pure-helper tests for `_logical_import_line` multi-line handling."""

    def test_single_line_returned_as_is(self) -> None:
        lines = ["import os  # noqa: F401", "x = 1"]
        self.assertEqual(_logical_import_line(lines, 1), "import os  # noqa: F401")

    def test_multi_line_paren_joined(self) -> None:
        lines = [
            "from conftest import (",
            "    ROOT,  # noqa: F401",
            "    other,",
            ")",
        ]
        result = _logical_import_line(lines, 1)
        self.assertIn("noqa: F401", result)
        self.assertIn(")", result)

    def test_out_of_range_returns_empty(self) -> None:
        self.assertEqual(_logical_import_line(["x"], 10), "")
        self.assertEqual(_logical_import_line([], 1), "")


class NoqaEndToEndTests(unittest.TestCase):
    """Build a tiny repo with noqa'd imports and verify they are NOT flagged."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="jgl_noqa_"))
        self.repo = self.tmp_root / "noqa_repo"
        self.repo.mkdir()

        # Three imports:
        #   1. `os` — unused AND no noqa → SHOULD be flagged
        #   2. `sys` — unused BUT has `# noqa: F401` → should NOT be flagged
        #   3. `json` — unused BUT has blanket `# noqa` → should NOT be flagged
        (self.repo / "mod.py").write_text(
            "import os\n"
            "import sys  # noqa: F401  path setup\n"
            "import json  # noqa\n"
            "\n"
            "x = 1\n",
            encoding="utf-8",
        )
        index_repo(self.repo, full=True)
        self.report = find_unused_imports(self.repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_plain_unused_still_flagged(self) -> None:
        bindings = {u.binding for u in self.report.unused}
        self.assertIn("os", bindings)

    def test_f401_suppressed_import_not_flagged(self) -> None:
        bindings = {u.binding for u in self.report.unused}
        self.assertNotIn("sys", bindings)

    def test_blanket_noqa_suppressed_import_not_flagged(self) -> None:
        bindings = {u.binding for u in self.report.unused}
        self.assertNotIn("json", bindings)


if __name__ == "__main__":
    unittest.main()
