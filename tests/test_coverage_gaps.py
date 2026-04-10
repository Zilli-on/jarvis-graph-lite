"""Tests for find_coverage_gaps (v0.8).

Builds a synthetic repo with src + tests structure and asserts that:
  - test entry points are discovered by file path AND name pattern
  - reachable symbols are computed via multi-source forward BFS
  - gaps are exactly the public symbols not in the reachable set
  - test files themselves are excluded from the gap pool
  - private and dunder symbols are excluded from the gap pool
  - methods on Test* classes pull setUp/tearDown into the reach set
  - empty test directory is reported with a friendly note
  - sort order is complexity desc → line_count desc → path

Layout:
  src_repo/
    foo.py:        def covered_func(); def uncovered_func(); class Foo
    bar.py:        def helper(); def deep_helper()
    baz.py:        def transitively_covered() (called by covered_func)
    private_mod.py: def _internal()  # private — not in pool either way
    tests/
      test_foo.py: def test_covered() → calls covered_func
                    def test_class()  → instantiates Foo
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from conftest import cleanup  # noqa: F401 (path setup)

from jarvis_graph.coverage_gap_engine import find_coverage_gaps
from jarvis_graph.indexer import index_repo


def _make_coverage_repo() -> tuple[Path, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_coverage_"))
    repo = tmp_root / "cov_repo"
    repo.mkdir()
    # baz: leaf helper, only reachable via foo.covered_func
    (repo / "baz.py").write_text(
        "def transitively_covered() -> int:\n"
        "    return 99\n",
        encoding="utf-8",
    )
    # bar: contains an uncovered helper and a deep one
    (repo / "bar.py").write_text(
        "def helper() -> int:\n"
        "    return 1\n"
        "\n"
        "def deep_helper() -> int:\n"
        "    if True:\n"
        "        return 2\n"
        "    return 3\n",
        encoding="utf-8",
    )
    # foo: covered_func calls into baz; uncovered_func is dead-to-tests; Foo is a covered class
    (repo / "foo.py").write_text(
        "from baz import transitively_covered\n"
        "\n"
        "def covered_func() -> int:\n"
        "    return transitively_covered() + 1\n"
        "\n"
        "def uncovered_func() -> int:\n"
        "    if True:\n"
        "        return 1\n"
        "    return 2\n"
        "\n"
        "class Foo:\n"
        "    def method(self) -> int:\n"
        "        return 5\n",
        encoding="utf-8",
    )
    # private_mod: defines a private function — should not appear in pool
    (repo / "private_mod.py").write_text(
        "def _internal() -> int:\n"
        "    return 42\n",
        encoding="utf-8",
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_foo.py").write_text(
        "from foo import covered_func, Foo\n"
        "\n"
        "def test_covered() -> None:\n"
        "    assert covered_func() == 100\n"
        "\n"
        "def test_class() -> None:\n"
        "    f = Foo()\n"
        "    assert f.method() == 5\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


def _make_no_tests_repo() -> tuple[Path, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_cov_empty_"))
    repo = tmp_root / "no_tests"
    repo.mkdir()
    (repo / "alpha.py").write_text(
        "def alpha() -> int:\n"
        "    return 1\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


def _make_test_class_repo() -> tuple[Path, Path]:
    """A unittest.TestCase-style suite where the test methods don't start
    with `test_` but live on a class whose name starts with `Test`. The
    setUp method should pull `fixture_helper` into the reach set."""
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_cov_unittest_"))
    repo = tmp_root / "tc_repo"
    repo.mkdir()
    (repo / "module.py").write_text(
        "def fixture_helper() -> int:\n"
        "    return 7\n"
        "\n"
        "def production_func() -> int:\n"
        "    return fixture_helper() + 1\n",
        encoding="utf-8",
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_module.py").write_text(
        "import unittest\n"
        "from module import fixture_helper, production_func\n"
        "\n"
        "class TestModule(unittest.TestCase):\n"
        "    def setUp(self) -> None:\n"
        "        self.h = fixture_helper()\n"
        "\n"
        "    def test_production(self) -> None:\n"
        "        self.assertEqual(production_func(), 8)\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


class CoverageGapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_coverage_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_test_entry_points_discovered(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        # Two test functions in the test file
        self.assertEqual(rep.test_entry_points, 2)

    def test_pool_excludes_test_files_and_private(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        # Public pool: covered_func, uncovered_func, transitively_covered,
        # helper, deep_helper, Foo (class), Foo.method = 7 symbols.
        # _internal is private → excluded. Test functions are in test files
        # → excluded.
        self.assertEqual(rep.total_public_symbols, 7)

    def test_covered_symbols_are_not_gaps(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        gap_qnames = {g.qualified_name for g in rep.gaps}
        # Direct test calls
        self.assertNotIn("foo.covered_func", gap_qnames)
        self.assertNotIn("foo.Foo", gap_qnames)
        self.assertNotIn("foo.Foo.method", gap_qnames)
        # Transitive via covered_func → transitively_covered
        self.assertNotIn("baz.transitively_covered", gap_qnames)

    def test_uncovered_symbols_are_in_gaps(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        gap_qnames = {g.qualified_name for g in rep.gaps}
        self.assertIn("foo.uncovered_func", gap_qnames)
        self.assertIn("bar.helper", gap_qnames)
        self.assertIn("bar.deep_helper", gap_qnames)

    def test_coverage_pct_is_reasonable(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        # 4 reached / 7 total ≈ 57%. Allow a small wobble.
        self.assertGreater(rep.coverage_pct, 50.0)
        self.assertLess(rep.coverage_pct, 70.0)

    def test_min_complexity_filters_simple_gaps(self) -> None:
        rep_all = find_coverage_gaps(self.repo, min_complexity=1)
        rep_complex = find_coverage_gaps(self.repo, min_complexity=2)
        # bar.helper has complexity 1, so it's in the all-list but not in
        # the complex-list. uncovered_func has cmplx 2 (one if branch).
        all_names = {g.qualified_name for g in rep_all.gaps}
        complex_names = {g.qualified_name for g in rep_complex.gaps}
        self.assertIn("bar.helper", all_names)
        self.assertNotIn("bar.helper", complex_names)
        self.assertIn("foo.uncovered_func", complex_names)

    def test_limit_caps_results(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1, limit=1)
        self.assertEqual(len(rep.gaps), 1)

    def test_sort_complexity_first_then_loc(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        # First entry should be the most complex gap.
        complexities = [g.complexity for g in rep.gaps]
        self.assertEqual(complexities, sorted(complexities, reverse=True))

    def test_caller_count_is_reported(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        gaps_by_name = {g.qualified_name: g for g in rep.gaps}
        # uncovered_func has zero callers anywhere.
        self.assertIn("foo.uncovered_func", gaps_by_name)
        self.assertEqual(gaps_by_name["foo.uncovered_func"].caller_count, 0)


class NoTestsRepoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_no_tests_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_no_test_entry_points_returns_friendly_note(self) -> None:
        rep = find_coverage_gaps(self.repo)
        self.assertEqual(rep.test_entry_points, 0)
        self.assertEqual(len(rep.gaps), 0)
        self.assertIn("no test entry points", rep.note)


class TestClassFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_test_class_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_setup_pulls_fixture_into_reach_set(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        gap_qnames = {g.qualified_name for g in rep.gaps}
        # fixture_helper is called only from setUp on a TestModule class.
        # It should be reached because setUp is treated as a test entry point.
        self.assertNotIn("module.fixture_helper", gap_qnames)
        self.assertNotIn("module.production_func", gap_qnames)


def _make_subject_tests_suffix_repo() -> tuple[Path, Path]:
    """Same shape as the TestModule fixture but using the `<Subject>Tests`
    suffix convention (e.g. `WidgetParserTests(unittest.TestCase)`) instead
    of the `Test*` prefix. The whole jarvis-graph-lite test suite uses this
    convention; before v0.9.2, methods on these classes were not recognised
    as test entry points so all production code reachable only via them was
    falsely flagged as a coverage gap."""
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_cov_suffix_"))
    repo = tmp_root / "tcs_repo"
    repo.mkdir()
    (repo / "widget.py").write_text(
        "def widget_helper() -> int:\n"
        "    return 11\n"
        "\n"
        "def widget_production() -> int:\n"
        "    return widget_helper() + 2\n",
        encoding="utf-8",
    )
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_widget.py").write_text(
        "import unittest\n"
        "from widget import widget_helper, widget_production\n"
        "\n"
        "class WidgetParserTests(unittest.TestCase):\n"
        "    def setUp(self) -> None:\n"
        "        self.h = widget_helper()\n"
        "\n"
        "    def test_production(self) -> None:\n"
        "        self.assertEqual(widget_production(), 13)\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


class SubjectTestsSuffixCoverageTests(unittest.TestCase):
    """Regression: methods on `<Subject>Tests` unittest classes (suffix
    convention) must be recognised as test entry points so the production
    code they reach isn't flagged as a coverage gap. Caught while dogfooding
    on jarvis-graph-lite itself, where every test class uses the suffix
    convention and `summarize` was being flagged even after a test was
    written for it."""

    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_subject_tests_suffix_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_subject_tests_methods_are_entry_points(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        # Two methods on WidgetParserTests: setUp + test_production.
        self.assertEqual(rep.test_entry_points, 2)

    def test_production_reached_through_suffix_class(self) -> None:
        rep = find_coverage_gaps(self.repo, min_complexity=1)
        gap_qnames = {g.qualified_name for g in rep.gaps}
        self.assertNotIn(
            "widget.widget_production",
            gap_qnames,
            "widget_production is called from a method on WidgetParserTests "
            "and must be reached",
        )
        self.assertNotIn(
            "widget.widget_helper",
            gap_qnames,
            "widget_helper is called from setUp on WidgetParserTests and "
            "must be reached",
        )


if __name__ == "__main__":
    unittest.main()
