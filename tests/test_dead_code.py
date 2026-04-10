"""find_dead_code engine smoke tests."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from conftest import cleanup, prepare_extended_repo

# Make `src/` importable without installing the package.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_graph.dead_code_engine import find_dead_code  # noqa: E402
from jarvis_graph.indexer import index_repo  # noqa: E402


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


class SameFileDispatchDictTests(unittest.TestCase):
    """Regression: a function registered in a dict literal *inside its own
    module* must not be flagged as dead. Caught while dogfooding the tool
    on its own cli.py — `yellow`/`magenta`/`cyan` were defined and used in
    the same file via `_KIND_COLOR = {"function": cyan, ...}` and the
    textual scan was excluding the own file entirely."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="jgl_dead_test_"))
        self.repo = self.tmp_root / "repo"
        self.repo.mkdir()
        # Single-file repo: `paint_red` is defined and registered in a dict
        # literal in the same file. No call site, no other file references.
        # Old behaviour: flagged as dead. New behaviour: alive (own-file
        # count = 2, definition + dict literal).
        (self.repo / "palette.py").write_text(
            "def paint_red(text: str) -> str:\n"
            "    return f'\\033[31m{text}\\033[0m'\n"
            "\n"
            "def paint_blue(text: str) -> str:\n"
            "    return f'\\033[34m{text}\\033[0m'\n"
            "\n"
            "_REGISTRY = {'red': paint_red, 'blue': paint_blue}\n",
            encoding="utf-8",
        )
        # Also include a truly dead function in the same file: only the
        # def line mentions it, no dict literal, no other reference.
        (self.repo / "dead_in_same_file.py").write_text(
            "def truly_lonely() -> int:\n"
            "    return 1\n",
            encoding="utf-8",
        )
        index_repo(self.repo, full=True)
        self.report = find_dead_code(self.repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_dict_literal_registered_helper_is_alive(self) -> None:
        flagged_names = {d.name for d in self.report.dead}
        self.assertNotIn(
            "paint_red",
            flagged_names,
            "paint_red is registered in _REGISTRY in its own file — must NOT be flagged dead",
        )
        self.assertNotIn(
            "paint_blue",
            flagged_names,
            "paint_blue is registered in _REGISTRY in its own file — must NOT be flagged dead",
        )

    def test_truly_lonely_still_flagged(self) -> None:
        flagged = {(d.name, d.rel_path) for d in self.report.dead}
        self.assertIn(
            ("truly_lonely", "dead_in_same_file.py"),
            flagged,
            "truly_lonely has only its own def-line mention — should still flag",
        )


class SubjectTestsClassConventionTests(unittest.TestCase):
    """Regression: `<Subject>Tests` unittest classes (the suffix convention,
    e.g. `MyParserTests(TestCase)`) were previously flagged as dead because
    the test-name filter only caught the `Test*` prefix convention. Caught
    while dogfooding the tool on its own test suite, where every test class
    uses the suffix convention."""

    def setUp(self) -> None:
        self.tmp_root = Path(tempfile.mkdtemp(prefix="jgl_dead_test_"))
        self.repo = self.tmp_root / "repo"
        self.repo.mkdir()
        # A unittest module that uses the `<Subject>Tests` suffix. The
        # framework discovers this via reflection so there are no static
        # callers anywhere.
        (self.repo / "test_widget.py").write_text(
            "import unittest\n"
            "\n"
            "class WidgetParserTests(unittest.TestCase):\n"
            "    def test_smoke(self) -> None:\n"
            "        self.assertTrue(True)\n",
            encoding="utf-8",
        )
        index_repo(self.repo, full=True)
        self.report = find_dead_code(self.repo)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def test_subject_tests_class_is_not_flagged(self) -> None:
        flagged_names = {d.name for d in self.report.dead}
        self.assertNotIn(
            "WidgetParserTests",
            flagged_names,
            "<Subject>Tests classes are unittest entry points and must NOT be flagged",
        )


if __name__ == "__main__":
    unittest.main()
