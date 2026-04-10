"""Tests for find_refactor_priority (v0.11).

Four layers of coverage:
  1. Pure scoring helpers (_complexity_score, _size_score, _caller_score)
  2. Path classifier (_is_test_path) — Windows + POSIX, fixtures, prefix/suffix
  3. Pre-filter behaviour (trivial, test-file, dunder, private)
  4. Composite scoring — weight_factor suppresses trivial-but-popular
     functions and untested complex functions rise above tested ones.
  5. Report metadata (skipped counts, threshold, limit, note)
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from conftest import cleanup  # noqa: F401 (path setup)

from jarvis_graph.indexer import index_repo
from jarvis_graph.refactor_priority_engine import (
    _caller_score,
    _complexity_score,
    _is_test_path,
    _size_score,
    find_refactor_priority,
)


# ---------------------------------------------------------------------------
# Layer 1: pure scoring helpers
# ---------------------------------------------------------------------------


class ScoringHelperTests(unittest.TestCase):
    """Pure functions — no DB, no fixtures, just math."""

    def test_complexity_score_zero(self) -> None:
        self.assertEqual(_complexity_score(0), 0.0)

    def test_complexity_score_mid(self) -> None:
        # 25 / 50 * 100 = 50
        self.assertEqual(_complexity_score(25), 50.0)

    def test_complexity_score_saturates_at_50(self) -> None:
        self.assertEqual(_complexity_score(50), 100.0)
        self.assertEqual(_complexity_score(200), 100.0)

    def test_size_score_zero(self) -> None:
        self.assertEqual(_size_score(0), 0.0)

    def test_size_score_mid(self) -> None:
        # 250 / 500 * 100 = 50
        self.assertEqual(_size_score(250), 50.0)

    def test_size_score_saturates_at_500(self) -> None:
        self.assertEqual(_size_score(500), 100.0)
        self.assertEqual(_size_score(5000), 100.0)

    def test_caller_score_zero_callers(self) -> None:
        self.assertEqual(_caller_score(0), 0.0)

    def test_caller_score_log_curve_one_caller(self) -> None:
        # log2(1 + 1) * 25 = 25
        self.assertEqual(_caller_score(1), 25.0)

    def test_caller_score_log_curve_three_callers(self) -> None:
        # log2(3 + 1) * 25 = 50
        self.assertEqual(_caller_score(3), 50.0)

    def test_caller_score_saturates_at_100(self) -> None:
        # log2(65) * 25 ≈ 150 → capped at 100
        self.assertEqual(_caller_score(64), 100.0)
        self.assertEqual(_caller_score(200), 100.0)


# ---------------------------------------------------------------------------
# Layer 2: test path classifier
# ---------------------------------------------------------------------------


class IsTestPathTests(unittest.TestCase):
    def test_tests_dir_prefix(self) -> None:
        self.assertTrue(_is_test_path("tests/test_foo.py"))

    def test_tests_dir_nested(self) -> None:
        self.assertTrue(_is_test_path("src/tests/foo.py"))

    def test_test_file_prefix(self) -> None:
        self.assertTrue(_is_test_path("test_foo.py"))

    def test_test_file_suffix(self) -> None:
        self.assertTrue(_is_test_path("foo_test.py"))

    def test_windows_separator(self) -> None:
        self.assertTrue(_is_test_path("tests\\test_foo.py"))
        self.assertTrue(_is_test_path("src\\tests\\foo.py"))

    def test_fixtures_dir_under_tests(self) -> None:
        self.assertTrue(_is_test_path("tests/fixtures/sample.py"))

    def test_fixtures_dir_anywhere(self) -> None:
        # `fixtures/` alone counts — these files aren't production code.
        self.assertTrue(_is_test_path("pkg/fixtures/thing.py"))

    def test_production_code_not_flagged(self) -> None:
        self.assertFalse(_is_test_path("src/foo.py"))
        self.assertFalse(_is_test_path("module/bar.py"))
        self.assertFalse(_is_test_path("app.py"))

    def test_similar_but_not_test(self) -> None:
        # "contest.py" contains "test" but shouldn't match
        self.assertFalse(_is_test_path("contest.py"))
        # "latest.py" ends with "test" but not "_test.py"
        self.assertFalse(_is_test_path("latest.py"))

    def test_case_insensitive(self) -> None:
        self.assertTrue(_is_test_path("Tests/Test_Foo.py"))
        self.assertTrue(_is_test_path("TEST_FOO.PY"))


# ---------------------------------------------------------------------------
# Layer 3 + 4: integration via synthetic repo
# ---------------------------------------------------------------------------


def _make_refactor_repo() -> tuple[Path, Path]:
    """Build a repo that exercises every pre-filter + the weight_factor.

    Contents:
      trivial_helper.py:
        def bold(s): return s           # trivial: cplx 1, 2 lines
      mixed.py:
        def _private_complex(x): ...    # private, should be skipped
        def public_simple(): return 1   # trivial, should be skipped
      complex_untested.py:
        def monster(a, b, c): ...       # non-trivial, no test reaches it
      complex_tested.py:
        def beast(a, b, c): ...         # SAME shape as monster, but tested
      caller.py:
        calls bold, monster, beast — so they all have >=1 caller
      tests/test_beast.py:
        calls beast only — monster stays untested
    """
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_refactor_"))
    repo = tmp_root / "refactor_repo"
    repo.mkdir()

    # Trivial helper — many callers, no tests, but still NOT a refactor
    # target because there's nothing to refactor. Pre-filter must drop it.
    (repo / "trivial_helper.py").write_text(
        "def bold(s):\n"
        "    return s\n",
        encoding="utf-8",
    )

    # Mixed file with a private function (should be skipped) and a
    # trivial public (should also be skipped).
    (repo / "mixed.py").write_text(
        "def _private_complex(x):\n"
        "    if x > 0:\n"
        "        if x > 10:\n"
        "            return 2\n"
        "        return 1\n"
        "    elif x < 0:\n"
        "        return -1\n"
        "    return 0\n"
        "\n"
        "def public_simple():\n"
        "    return 1\n",
        encoding="utf-8",
    )

    # Non-trivial complex function, NOT reached from tests.
    _complex_body = (
        "def {name}(a, b, c):\n"
        "    total = 0\n"
        "    if a > 0:\n"
        "        total += 1\n"
        "    elif a < 0:\n"
        "        total -= 1\n"
        "    if b > 0:\n"
        "        total += 2\n"
        "    elif b < 0:\n"
        "        total -= 2\n"
        "    for i in range(10):\n"
        "        if i % 2 == 0:\n"
        "            total += i\n"
        "        elif i % 3 == 0:\n"
        "            total -= i\n"
        "    try:\n"
        "        return total / c\n"
        "    except ZeroDivisionError:\n"
        "        return 0\n"
    )
    (repo / "complex_untested.py").write_text(
        _complex_body.format(name="monster"),
        encoding="utf-8",
    )
    # SAME code under a different name — so cplx + line_count match exactly.
    (repo / "complex_tested.py").write_text(
        _complex_body.format(name="beast"),
        encoding="utf-8",
    )

    # Caller that uses everything (gives them real caller_count > 0).
    (repo / "caller.py").write_text(
        "from trivial_helper import bold\n"
        "from complex_untested import monster\n"
        "from complex_tested import beast\n"
        "\n"
        "def use_bold():\n"
        "    return bold('hi')\n"
        "\n"
        "def use_monster():\n"
        "    return monster(1, 2, 3)\n"
        "\n"
        "def use_beast():\n"
        "    return beast(1, 2, 3)\n",
        encoding="utf-8",
    )

    # Tests reach `beast` only — `monster` stays untested.
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_beast.py").write_text(
        "from complex_tested import beast\n"
        "from caller import use_beast\n"
        "\n"
        "def test_beast():\n"
        "    assert use_beast() is not None\n"
        "    assert beast(1, 2, 3) is not None\n",
        encoding="utf-8",
    )

    index_repo(repo, full=True)
    return tmp_root, repo


class RefactorPriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_refactor_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    # ---- pre-filter layer ------------------------------------------------

    def test_trivial_helper_excluded(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        qnames = {c.qualified_name for c in rep.candidates}
        # `bold` has cplx 1 + line_count 2 → trivial pre-filter drops it.
        self.assertFalse(
            any("bold" in q for q in qnames),
            f"trivial `bold` should not be in candidates: {qnames}",
        )
        self.assertGreater(rep.skipped_trivial, 0)

    def test_trivial_public_simple_excluded(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        qnames = {c.qualified_name for c in rep.candidates}
        self.assertFalse(
            any(q.endswith(".public_simple") for q in qnames),
            f"public_simple is trivial (1 line) — must be skipped: {qnames}",
        )

    def test_private_function_excluded(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        qnames = {c.qualified_name for c in rep.candidates}
        self.assertFalse(
            any("_private_complex" in q for q in qnames),
            f"private symbols must be skipped: {qnames}",
        )

    def test_test_files_excluded(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        qnames = {c.qualified_name for c in rep.candidates}
        self.assertFalse(
            any("test_beast" in q for q in qnames),
            f"symbols under tests/ must be skipped: {qnames}",
        )
        self.assertGreater(rep.skipped_test, 0)

    # ---- composite scoring layer ----------------------------------------

    def test_monster_and_beast_both_ranked(self) -> None:
        """Both non-trivial functions should survive pre-filters and appear
        in the candidate list when threshold=0."""
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        by_name = {c.qualified_name: c for c in rep.candidates}
        self.assertTrue(
            any("monster" in q for q in by_name),
            f"monster should be a candidate: {list(by_name.keys())}",
        )
        self.assertTrue(
            any("beast" in q for q in by_name),
            f"beast should be a candidate: {list(by_name.keys())}",
        )

    def test_untested_ranks_strictly_higher_than_tested(self) -> None:
        """Same shape, same complexity, same line count — the only
        difference is test coverage. The untested version must score
        strictly higher."""
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        monster = next(
            (c for c in rep.candidates if "monster" in c.qualified_name), None
        )
        beast = next(
            (c for c in rep.candidates if "beast" in c.qualified_name), None
        )
        self.assertIsNotNone(monster, "monster missing from candidates")
        self.assertIsNotNone(beast, "beast missing from candidates")
        # Sanity: same shape
        self.assertEqual(monster.complexity, beast.complexity)
        self.assertEqual(monster.line_count, beast.line_count)
        # The meaningful assertion
        self.assertGreater(
            monster.priority,
            beast.priority,
            "untested monster should score higher than tested beast",
        )
        self.assertTrue(monster.is_untested)
        self.assertFalse(beast.is_untested)

    def test_weight_factor_scales_untested_penalty(self) -> None:
        """Monster's untested_penalty must equal 100 * weight_factor, not
        a flat 100. This is the whole point of the v0.11 refinement."""
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        monster = next(
            (c for c in rep.candidates if "monster" in c.qualified_name), None
        )
        self.assertIsNotNone(monster)
        # Monster is non-trivial but not extreme — weight_factor is well
        # below 1, so the untested penalty should be well below 100.
        self.assertLess(
            monster.untested_penalty,
            100.0,
            "untested_penalty must be scaled by weight_factor, not flat 100",
        )
        self.assertGreater(
            monster.untested_penalty, 0.0, "untested monster must get some penalty"
        )

    def test_tested_symbol_has_zero_untested_penalty(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        beast = next(
            (c for c in rep.candidates if "beast" in c.qualified_name), None
        )
        self.assertIsNotNone(beast)
        self.assertEqual(beast.untested_penalty, 0.0)

    def test_caller_count_recorded(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        by_name = {c.qualified_name: c for c in rep.candidates}
        monster_qname = next(q for q in by_name if "monster" in q)
        beast_qname = next(q for q in by_name if "beast" in q)
        # Both are called exactly once from caller.py
        self.assertGreaterEqual(by_name[monster_qname].caller_count, 1)
        self.assertGreaterEqual(by_name[beast_qname].caller_count, 1)

    def test_reasons_populated(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        for c in rep.candidates:
            self.assertGreater(
                len(c.reasons), 0, f"{c.qualified_name} has no reasons"
            )

    def test_untested_reason_tag_present(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        monster = next(
            (c for c in rep.candidates if "monster" in c.qualified_name), None
        )
        self.assertIsNotNone(monster)
        self.assertTrue(
            any("untested" in r for r in monster.reasons),
            f"monster reasons should include 'untested': {monster.reasons}",
        )

    # ---- report metadata ------------------------------------------------

    def test_total_evaluated_is_positive(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        self.assertGreater(rep.total_evaluated, 0)

    def test_threshold_field_populated(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=42.5)
        self.assertEqual(rep.threshold, 42.5)

    def test_very_high_threshold_returns_nothing(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=999.0)
        self.assertEqual(len(rep.candidates), 0)

    def test_limit_caps_results(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0, limit=1)
        self.assertLessEqual(len(rep.candidates), 1)

    def test_sort_order_descending(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        priorities = [c.priority for c in rep.candidates]
        self.assertEqual(priorities, sorted(priorities, reverse=True))


# ---------------------------------------------------------------------------
# Layer 5: no-tests fallback
# ---------------------------------------------------------------------------


def _make_no_tests_refactor_repo() -> tuple[Path, Path]:
    tmp_root = Path(tempfile.mkdtemp(prefix="jgl_refactor_notests_"))
    repo = tmp_root / "notest_repo"
    repo.mkdir()
    # A single non-trivial function so we actually have something to rank.
    (repo / "thing.py").write_text(
        "def complex_thing(x, y):\n"
        "    total = 0\n"
        "    if x > 0:\n"
        "        total += 1\n"
        "    elif x < 0:\n"
        "        total -= 1\n"
        "    if y > 0:\n"
        "        total += 2\n"
        "    return total\n",
        encoding="utf-8",
    )
    index_repo(repo, full=True)
    return tmp_root, repo


class NoTestsRefactorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_root, self.repo = _make_no_tests_refactor_repo()

    def tearDown(self) -> None:
        cleanup(self.tmp_root)

    def test_note_when_no_test_entry_points(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        self.assertIn("no test entry points", rep.note)

    def test_everything_flagged_untested(self) -> None:
        rep = find_refactor_priority(self.repo, min_priority=0.0)
        # Every candidate in this repo must be flagged untested
        for c in rep.candidates:
            self.assertTrue(
                c.is_untested, f"{c.qualified_name} should be untested"
            )


if __name__ == "__main__":
    unittest.main()
